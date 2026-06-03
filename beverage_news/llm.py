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


# ── Semantic deduplication ────────────────────────────────────────────────────

_DEDUP_SYSTEM = """\
You are a news deduplication assistant for a beverage industry monitor.

Your task: given a numbered list of article titles, identify which ones cover the \
SAME specific news event — even when written with different words or angles.

Two titles cover the SAME event when they report the same specific announcement, \
result, deal, or development (e.g. both about Coca-Cola Q1 2026 earnings).

They do NOT cover the same event if they are about the same general topic but \
different specific events (e.g. Q1 results vs Q2 results, or two different product launches).

Respond with ONLY a valid JSON array of arrays. Each inner array contains the \
1-based indices of titles covering the same event. Only include groups of 2 or more. \
If no duplicates exist, respond with: []

Example: [[1,3],[2,5]] means titles 1 & 3 are the same event, and 2 & 5 are the same event.
No extra text, no markdown.
"""


def semantic_dedup_articles(articles: list) -> tuple:
    """
    Detect articles covering the same event via ONE batch Haiku call.
    Groups articles by primary company + primary segment, then sends ALL
    groups in a single request — no per-group API calls.
    Returns (deduplicated_articles, n_merged).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return articles, 0

    try:
        client = _get_client()
    except Exception:
        return articles, 0

    time.sleep(_CALL_DELAY_SECONDS)  # Respect rate limit before batch call

    from collections import defaultdict
    groups = defaultdict(list)
    for idx, article in enumerate(articles):
        # Group only by company — same event can have different segment tags
        company = article.companies[0] if article.companies else "__none__"
        groups[company].append(idx)

    # Collect only groups with 2+ articles
    active_groups = {k: v for k, v in groups.items() if len(v) >= 2}
    if not active_groups:
        return articles, 0

    # Build one prompt with all groups
    lines = []
    group_keys = []  # ordered list matching prompt sections
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

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=[{"type": "text", "text": _DEDUP_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = response.content[0].text.strip()
        dup_pairs = json.loads(raw)
    except Exception as exc:
        logger.warning("Semantic dedup batch call failed: %s", exc)
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
        except (ValueError, IndexError):
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
