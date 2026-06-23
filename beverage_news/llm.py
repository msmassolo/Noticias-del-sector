"""
LLM integration for article summarization and relevance classification.

Uses Anthropic Claude with prompt caching to minimize cost:
- System prompt is cached across all articles in a run (~70% cost reduction).
- Results are cached locally in llm_cache.json (keyed by URL hash) to avoid
  reprocessing articles already analyzed in the same day.

Set ANTHROPIC_API_KEY in .env before use.
"""

import hashlib
import json
import logging
import os
import time
from datetime import date
from pathlib import Path

# Delay between API calls to stay within free-tier rate limits (~5 RPM = 1 req/12s).
# Set to 1 after reaching Anthropic Tier 1 (50 RPM).
_CALL_DELAY_SECONDS = 13

logger = logging.getLogger(__name__)

LLM_CACHE_FILE = Path("data/llm_cache.json")
MAX_BODY_FOR_LLM = 6000  # chars sent to LLM (enough context, lower cost)

_CLASSIFY_AND_SUMMARIZE_SYSTEM = """\
Sos un filtro editorial para un briefing de inteligencia ejecutiva de la industria de bebidas \
de consumo masivo, usado por directivos de una empresa argentina.

Tu tarea es DOBLE en una sola respuesta:
1. Determinar si la noticia es RELEVANTE para este briefing ejecutivo.
2. Si es relevante, generar un resumen ejecutivo en español.

RELEVANTE: M&A, adquisiciones, IPO, resultados financieros, lanzamientos de productos, \
estrategia corporativa, regulación, cambios de distribución, sustentabilidad con impacto \
estratégico, innovación en packaging, tendencias de mercado con implicancias de negocio, \
movimientos de competidores clave, tecnología aplicada a la industria.

NO RELEVANTE: videos virales, incidentes con animales en plantas, promociones de \
supermercados no vinculadas a bebidas, recetas, consejos de salud, deportes, \
entretenimiento, noticias sensacionalistas sin valor estratégico, precios de canastas \
básicas sin análisis de industria, contenido de lifestyle.

Respondé ÚNICAMENTE con un JSON válido, sin texto adicional ni markdown:
- Si es relevante: {"relevant": true, "summary": "3-5 oraciones ejecutivas en español"}
- Si no es relevante: {"relevant": false, "summary": ""}

El resumen debe explicar: qué pasó, quién está involucrado, por qué importa a la \
industria de bebidas, cifras o mercados clave. Directo, sin "El artículo dice..." ni \
"Esta nota...". Como si le explicaras a un colega ejecutivo.
"""

_client = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "anthropic SDK not installed. Run: "
            "python -m pip install anthropic --target \"c:\\Proyectos Claude\\Bibliotecas\\Bibliotecas py\""
        )
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY not set in environment / .env")
    _client = anthropic.Anthropic(api_key=api_key)
    return _client


# ── Local cache ────────────────────────────────────────────────────────────────

def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _load_cache() -> dict:
    if not LLM_CACHE_FILE.exists():
        return {}
    try:
        with open(LLM_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        today = date.today().isoformat()
        # Evict entries from previous days
        return {k: v for k, v in data.items() if v.get("date") == today}
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    LLM_CACHE_FILE.parent.mkdir(exist_ok=True)
    with open(LLM_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ── Core summarization ─────────────────────────────────────────────────────────

MAX_EXCERPT_FOR_LLM = 500  # chars — enough for relevance+summary, 12x cheaper than 6000


def _classify_and_summarize_one(client, title: str, body: str) -> tuple:
    """
    Single Haiku call: classify relevance AND generate summary if relevant.
    Input uses only title + first 500 chars of body (cheap).
    Returns (is_relevant: bool, summary: str).
    """
    excerpt = body[:MAX_EXCERPT_FOR_LLM] if body else ""
    user_content = f"Título: {title}\n\nFragmento: {excerpt or '(sin cuerpo)'}"
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=350,
        system=[
            {
                "type": "text",
                "text": _CLASSIFY_AND_SUMMARIZE_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )
    raw = response.content[0].text.strip()
    # Strip markdown code fences that the model occasionally adds despite instructions
    clean = raw
    if clean.startswith("```"):
        lines = clean.split("\n")
        # Drop first line (```json / ```) and last ``` if present
        inner = lines[1:-1] if len(lines) > 2 and lines[-1].strip().startswith("```") else lines[1:]
        clean = "\n".join(inner).strip()
    try:
        result = json.loads(clean)
        return bool(result.get("relevant")), result.get("summary", "")
    except Exception:
        # Last resort: find a JSON object anywhere in the response
        import re as _re
        match = _re.search(r'\{[^{}]*"relevant"[^{}]*\}', clean, _re.DOTALL)
        if match:
            try:
                result = json.loads(match.group())
                return bool(result.get("relevant")), result.get("summary", "")
            except Exception:
                pass
        logger.warning("LLM classify JSON parse failed, treating as relevant: %r", raw[:80])
        return True, ""


def classify_and_summarize(articles: list) -> tuple[list, dict]:
    """
    Classify each article for executive relevance AND generate summary in one Haiku call.
    Uses only title + first 500 chars of body — ~67% cheaper than full summarization.
    Irrelevant articles are removed from the returned list.
    Returns (relevant_articles_with_summaries, diagnostics).
    """
    diagnostics = {
        "attempted": 0,
        "cached": 0,
        "generated": 0,
        "rejected_not_relevant": 0,
        "failed": 0,
        "skipped_no_key": False,
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("LLM classify+summarize skipped: ANTHROPIC_API_KEY not set")
        diagnostics["skipped_no_key"] = True
        for article in articles:
            article.llm_summary = ""
        return articles, diagnostics

    try:
        client = _get_client()
    except Exception as exc:
        logger.error("LLM client init failed: %s", exc)
        for article in articles:
            article.llm_summary = ""
        return articles, diagnostics

    cache = _load_cache()
    today = date.today().isoformat()
    relevant = []

    for article in articles:
        key = _cache_key(article.url)
        if key in cache:
            cached = cache[key]
            # Cache stores whether article was relevant
            if not cached.get("relevant", True):
                diagnostics["cached"] += 1
                diagnostics["rejected_not_relevant"] += 1
                logger.debug("LLM cached rejection: %r", article.title[:60])
                continue
            article.llm_summary = cached.get("summary", "")
            relevant.append(article)
            diagnostics["cached"] += 1
            continue

        diagnostics["attempted"] += 1
        if diagnostics["attempted"] > 1:
            time.sleep(_CALL_DELAY_SECONDS)
        try:
            is_relevant, summary = _classify_and_summarize_one(client, article.title, article.body)
            cache[key] = {"relevant": is_relevant, "summary": summary, "date": today, "url": article.url}
            if is_relevant:
                article.llm_summary = summary
                relevant.append(article)
                diagnostics["generated"] += 1
                logger.debug("LLM classified RELEVANT: %r", article.title[:60])
            else:
                diagnostics["rejected_not_relevant"] += 1
                logger.info("LLM rejected (not executive-relevant): %r", article.title[:60])
        except Exception as exc:
            logger.warning("LLM classify failed for %r: %s", article.url, exc)
            article.llm_summary = ""
            relevant.append(article)  # Keep on failure to avoid losing content
            diagnostics["failed"] += 1

    _save_cache(cache)
    logger.info(
        "LLM classify+summarize: %d relevant, %d rejected, %d from cache, %d failed (of %d input)",
        diagnostics["generated"],
        diagnostics["rejected_not_relevant"],
        diagnostics["cached"],
        diagnostics["failed"],
        len(articles),
    )
    return relevant, diagnostics


# Keep summarize_articles as alias for backward compatibility
def summarize_articles(articles: list) -> tuple[list, dict]:
    return classify_and_summarize(articles)


# ── Area definitions ───────────────────────────────────────────────────────────

AREA_CONFIG = {
    "finanzas": {
        "label": "Finanzas",
        "topics": ["financial_results", "ma_and_strategy", "risk_crisis_reputation"],
        "focus": "resultados financieros, M&A, adquisiciones, riesgos y crisis corporativas",
    },
    "marketing": {
        "label": "Marketing",
        "topics": ["product_innovation", "marketing_innovation", "consumer_market_trends", "non_alcoholic_beverages", "alternative_ingredients"],
        "focus": "innovación de producto, campañas, tendencias del consumidor, bebidas sin alcohol y alternativas",
    },
    "supply_chain": {
        "label": "Supply Chain",
        "topics": ["supply_chain_commodities", "packaging_sustainability", "regulation_tax_policy"],
        "focus": "materias primas, logística, packaging, sustentabilidad y regulación/política",
    },
    "ventas": {
        "label": "Ventas / Comercial",
        "topics": ["distribution_execution", "consumer_market_trends", "marketing_innovation"],
        "focus": "distribución, canales (moderno/tradicional/HORECA), ejecución comercial y tendencias de consumo",
    },
}

_AREA_BRIEFINGS_SYSTEM = """\
Sos un analista de inteligencia de mercado senior para la industria de bebidas de consumo masivo \
en Argentina. Generás briefings ejecutivos diarios para cuatro áreas de la empresa.

Respondé SIEMPRE en español. Respondé SOLO con un JSON válido, sin texto adicional, sin markdown.

El JSON debe tener exactamente esta estructura:
{
  "finanzas": "2-3 oraciones sobre los temas más relevantes del día para Finanzas: resultados, M&A, riesgo financiero o crisis corporativa.",
  "marketing": "2-3 oraciones sobre los temas más relevantes del día para Marketing: lanzamientos, campañas, tendencias del consumidor, bebidas sin alcohol.",
  "supply_chain": "2-3 oraciones sobre los temas más relevantes del día para Supply Chain: materias primas, packaging, regulación, logística.",
  "ventas": "2-3 oraciones sobre los temas más relevantes del día para Ventas/Comercial: canales, distribución, ejecución en punto de venta, precio."
}

Si no hay noticias relevantes para un área en particular, escribí: "Sin novedades destacadas en este período."
El tono es ejecutivo y directo. Sin rodeos, sin elogios vacíos.
"""


def generate_area_briefings(articles: list) -> dict:
    """
    Single Sonnet call: generates one briefing per business area from today's articles.
    Returns dict: {area_key: briefing_text}. Falls back to empty strings on failure.
    """
    empty = {area: "" for area in AREA_CONFIG}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return empty

    try:
        client = _get_client()
    except Exception:
        return empty

    # Build compact article list — title + topic + company only (no summary, to keep prompt short)
    lines = []
    for i, a in enumerate(articles, 1):
        topics = ", ".join(getattr(a, "segments", [])[:2]) or "—"
        companies = ", ".join(getattr(a, "companies", [])[:2]) or "—"
        region = getattr(a, "region", "Mundial")
        lines.append(f"{i}. [{region}] {a.title} | {topics} | {companies}")

    articles_text = "\n".join(lines)
    user_msg = f"Noticias del día ({len(articles)} en total):\n\n{articles_text}\n\nGenerá los briefings por área."

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1200,
            system=[{"type": "text", "text": _AREA_BRIEFINGS_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        import re as _re
        raw = response.content[0].text.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        result = json.loads(raw)
        briefings = {area: result.get(area, "") for area in AREA_CONFIG}
        logger.info("Area briefings generated for %d areas", sum(1 for v in briefings.values() if v))
        return briefings
    except Exception as exc:
        logger.warning("Area briefings generation failed: %s", exc)
        return empty


# ── Weekly summary ─────────────────────────────────────────────────────────────

_WEEKLY_SUMMARY_SYSTEM = """\
Sos un analista de inteligencia competitiva senior para la industria de bebidas de consumo masivo \
en Argentina. Generás el resumen semanal ejecutivo de las noticias más importantes del sector.

Respondé SIEMPRE en español. Respondé SOLO con un JSON válido, sin texto adicional, sin markdown.

El JSON debe tener exactamente esta estructura:
{
  "resumen_general": "3-4 oraciones con los temas que dominaron la semana a nivel global y regional.",
  "finanzas": "2 oraciones sobre los eventos financieros, M&A o crisis de la semana.",
  "marketing": "2 oraciones sobre los lanzamientos, campañas o tendencias de consumidor más destacados.",
  "supply_chain": "2 oraciones sobre materias primas, regulación, packaging o logística de la semana.",
  "ventas": "2 oraciones sobre movimientos en canales, distribución o ejecución comercial.",
  "top_eventos": ["evento 1 en una oración", "evento 2", "evento 3", "evento 4", "evento 5"]
}

El tono es ejecutivo. Sin rodeos. Máximo 5 eventos destacados.
"""


WEEKLY_SUMMARY_MIN_DAYS = 4  # Don't generate until we have at least this many days of data


def generate_weekly_summary(weekly_log: dict) -> dict:
    """
    Single Sonnet call per week. Generates a weekly summary from the accumulated article log.
    weekly_log: {date_str: [{"title", "summary", "companies", "segments", "region"}]}
    Returns dict with keys: resumen_general, finanzas, marketing, supply_chain, ventas, top_eventos, days_available.
    Only generates when log has >= WEEKLY_SUMMARY_MIN_DAYS days of data.
    """
    empty = {
        "resumen_general": "",
        "finanzas": "",
        "marketing": "",
        "supply_chain": "",
        "ventas": "",
        "top_eventos": [],
        "days_available": 0,
    }

    if not weekly_log:
        return empty

    days_available = len(weekly_log)
    empty["days_available"] = days_available

    if days_available < WEEKLY_SUMMARY_MIN_DAYS:
        logger.info(
            "Weekly summary skipped: only %d/%d days of data available",
            days_available, WEEKLY_SUMMARY_MIN_DAYS,
        )
        return empty

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return empty

    try:
        client = _get_client()
    except Exception:
        return empty

    # Topic priority weights for selecting most important articles per day
    _TOPIC_PRIORITY = {
        "financial_results": 10, "ma_and_strategy": 9, "risk_crisis_reputation": 8,
        "regulation_tax_policy": 7, "distribution_execution": 6, "product_innovation": 5,
        "supply_chain_commodities": 5, "packaging_sustainability": 4,
        "marketing_innovation": 3, "consumer_market_trends": 3,
        "non_alcoholic_beverages": 2, "alternative_ingredients": 2, "company_news": 1,
    }

    def _article_importance(a):
        topic_score = max((_TOPIC_PRIORITY.get(t, 0) for t in a.get("segments", [])), default=0)
        company_bonus = 3 if a.get("companies") else 0
        local_bonus = 2 if a.get("region") == "Local" else 0
        return topic_score + company_bonus + local_bonus

    # Build compact summary — take top 15 most important articles per day
    lines = []
    total = 0
    for day_date in sorted(weekly_log.keys(), reverse=True):
        day_articles = sorted(weekly_log[day_date], key=_article_importance, reverse=True)[:15]
        lines.append(f"\n--- {day_date} ({len(weekly_log[day_date])} noticias, mostrando top {len(day_articles)}) ---")
        for a in day_articles:
            topics = ", ".join(a.get("segments", [])[:2]) or "—"
            companies = ", ".join(a.get("companies", [])[:2]) or "—"
            summary_text = (a.get("llm_summary") or a.get("summary") or "")[:120]
            lines.append(f"  - [{a.get('region','?')}] {a.get('title','')} | {topics} | {companies}")
            if summary_text:
                lines.append(f"    {summary_text}")
            total += 1

    articles_text = "\n".join(lines)
    days = len(weekly_log)
    user_msg = f"Resumen de la semana: {days} días, {total} noticias publicadas.\n{articles_text}\n\nGenerá el resumen semanal ejecutivo."

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1400,
            system=[{"type": "text", "text": _WEEKLY_SUMMARY_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = raw[:-3].strip()
        result = json.loads(raw)
        result["days_available"] = days_available
        logger.info("Weekly summary generated from %d days / %d articles", days, total)
        return result
    except Exception as exc:
        logger.warning("Weekly summary generation failed: %s", exc)
        return empty


# ── Semantic deduplication ────────────────────────────────────────────────────

_DEDUP_SYSTEM = """\
You are a news deduplication assistant for a beverage industry monitor.

Your task: given titles organized into numbered groups, identify which titles WITHIN \
the same group cover the SAME specific news event — even when written with different \
words or angles. Only compare titles inside the same group; never merge across groups.

Each title is labeled "G.P" where G is the group number and P is its position in the \
group (e.g. "3.2" = second title of group 3).

Two titles cover the SAME event when they report the same specific announcement, \
result, deal, or development (e.g. both about Coca-Cola Q1 2026 earnings, or both about \
the same acquisition).

They do NOT cover the same event if they are about the same general topic but \
different specific events (e.g. Q1 results vs Q2 results, or two different product launches).

Respond with ONLY a valid JSON array of arrays. Each inner array lists the "G.P" string \
labels of titles covering the same event. Only include groups of 2 or more labels. \
If no duplicates exist, respond with: []

Example: [["3.1","3.3"],["5.2","5.4"]] means titles 3.1 & 3.3 are the same event, and \
5.2 & 5.4 are the same event.
No extra text, no markdown.
"""


def semantic_dedup_articles(articles: list) -> tuple:
    """
    Detect articles covering the same event via ONE batch Haiku call.
    Groups by company first; articles sharing a company name in the title
    also get grouped together even if not formally tagged.
    Returns (deduplicated_articles, n_merged).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return articles, 0

    try:
        client = _get_client()
    except Exception:
        return articles, 0

    time.sleep(_CALL_DELAY_SECONDS)

    from collections import defaultdict
    groups = defaultdict(list)
    for idx, article in enumerate(articles):
        company = article.companies[0] if article.companies else "__none__"
        groups[company].append(idx)

    # Also group articles that share a company name in the title but may be tagged differently
    title_words = {}
    for idx, article in enumerate(articles):
        for company, indices in groups.items():
            if company != "__none__" and company.lower() in article.title.lower():
                if idx not in indices:
                    indices.append(idx)

    active_groups = {k: sorted(set(v)) for k, v in groups.items() if len(set(v)) >= 2}
    if not active_groups:
        return articles, 0

    lines = []
    group_keys = []
    for company, indices in active_groups.items():
        group_id = len(group_keys)
        group_keys.append((company, indices))
        lines.append(f"[Grupo {group_id}: {company}]")
        for local_i, global_i in enumerate(indices):
            lines.append(f"  {group_id}.{local_i + 1}. {articles[global_i].title}")

    prompt_body = "\n".join(lines)
    user_msg = (
        "Identificá cuáles títulos dentro de cada grupo cubren el MISMO evento específico.\n\n"
        + prompt_body
        + "\n\nRespondé SOLO con JSON: lista de listas con los IDs de duplicados. "
        "Formato: [[\"0.1\",\"0.3\"],[\"2.1\",\"2.2\"]] — "
        "donde el primer número es el grupo y el segundo la posición. "
        "Si no hay duplicados: []"
    )

    def _try_call():
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=[{"type": "text", "text": _DEDUP_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        if not raw:
            raise ValueError("empty response from dedup LLM")
        # Strip markdown fences
        if raw.startswith("```"):
            lines_r = raw.split("\n")
            raw = "\n".join(lines_r[1:-1] if lines_r[-1].strip().startswith("```") else lines_r[1:]).strip()
        return json.loads(raw)

    dup_pairs = None
    for attempt in range(2):  # 1 retry on failure
        try:
            if attempt > 0:
                time.sleep(3)
            dup_pairs = _try_call()
            break
        except Exception as exc:
            logger.warning("Semantic dedup attempt %d failed: %s", attempt + 1, exc)

    if dup_pairs is None:
        return articles, 0

    if not dup_pairs or not isinstance(dup_pairs, list):
        logger.info("Semantic dedup: no duplicate events found (1 batch call)")
        return articles, 0

    # Apply merges
    merge_into = {}
    for pair in dup_pairs:
        if not isinstance(pair, list) or len(pair) < 2:
            continue
        try:
            def _resolve(ref):
                parts = str(ref).split(".")
                g_id, l_pos = int(parts[0]), int(parts[1]) - 1
                _company, indices = group_keys[g_id]
                return indices[l_pos]

            keep_global = _resolve(pair[0])
            for other in pair[1:]:
                merge_global = _resolve(other)
                if merge_global != keep_global and merge_global not in merge_into:
                    merge_into[merge_global] = keep_global
        except (ValueError, IndexError) as exc:
            logger.warning("Semantic dedup: could not resolve pair %r (%s)", pair, exc)
            continue

    if not merge_into:
        return articles, 0

    n_merged = 0
    result = []
    for idx, article in enumerate(articles):
        if idx in merge_into:
            kept = articles[merge_into[idx]]
            kept.merged_sources.append(f"{article.source}|||{article.url}")
            n_merged += 1
            logger.info("Semantic dedup merged: %r → %r", article.title[:55], kept.title[:55])
        else:
            result.append(article)

    logger.info("Semantic dedup: %d merged (1 batch API call)", n_merged)
    return result, n_merged


# ── Dashboard QA ───────────────────────────────────────────────────────────────

_QA_SYSTEM = """\
Sos un editor senior de un monitor de noticias del sector de bebidas de consumo masivo \
para ejecutivos de una empresa argentina. Tu tarea es auditar el conjunto de noticias \
del día antes de publicarlas.

Respondé SIEMPRE en español. Respondé SOLO con un JSON válido, sin texto adicional, \
sin markdown, sin ```json.

El JSON debe tener exactamente esta estructura:
{
  "briefing": "2-3 oraciones resumiendo el día: qué temas dominan, qué empresa está en el centro, cuál es el evento más relevante.",
  "warnings": ["lista de alertas editoriales — puede estar vacía []"],
  "quality_score": número del 1 al 10
}

Alertas a detectar (incluir en warnings solo si aplican):
- Más del 40% de las noticias son de la misma empresa
- Dominancia de una sola región (>70% de un tipo)
- Noticias de bajo impacto que no aportan valor ejecutivo
- Ausencia total de noticias locales o regionales
- Temas estratégicos clave ausentes (financiero, regulatorio, M&A)
"""


def review_dashboard(articles: list) -> dict:
    """
    Single Sonnet call per run. Audits the final article set before publishing.
    Returns dict with keys: briefing, warnings, quality_score.
    Falls back to empty result if API fails or key not set.
    """
    empty = {"briefing": "", "warnings": [], "quality_score": 0}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("Dashboard QA skipped: ANTHROPIC_API_KEY not set")
        return empty

    try:
        client = _get_client()
    except Exception as exc:
        logger.error("LLM client init failed for QA: %s", exc)
        return empty

    # Build compact article list for the prompt
    lines = []
    for i, a in enumerate(articles, 1):
        companies = ", ".join(a.companies) if a.companies else "—"
        segments = ", ".join(a.segments[:2]) if a.segments else "—"
        region = getattr(a, "region", "Mundial")
        lines.append(f"{i}. [{region}] {a.title} | {a.source} | Empresas: {companies} | Tópicos: {segments}")

    articles_text = "\n".join(lines)
    user_msg = f"Estas son las {len(articles)} noticias del tablero de hoy:\n\n{articles_text}"

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            system=[{"type": "text", "text": _QA_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        import json as _json
        result = _json.loads(response.content[0].text.strip())
        logger.info("Dashboard QA: score=%s, warnings=%d", result.get("quality_score"), len(result.get("warnings", [])))
        return result
    except Exception as exc:
        logger.warning("Dashboard QA failed: %s", exc)
        return empty
