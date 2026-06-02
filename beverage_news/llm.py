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

_SYSTEM_PROMPT = """\
You are an editorial analyst for a beverage industry news monitor used by executives at a \
consumer goods company in Argentina. Your output is always in Spanish.

Your task: given the title, summary, and article body of a news article, produce a \
comprehensive editorial summary that explains:
- What happened (the main fact or announcement)
- Who is involved (companies, brands, executives if mentioned)
- Why it matters to the beverage industry (market impact, strategic relevance, implications)
- Any relevant numbers, markets, or timeframes mentioned

Write 3–5 sentences. Be direct and informative — this replaces the original article summary \
in a daily briefing read by busy executives. Do not use bullet points. Do not start with \
"El artículo..." or "Esta nota...". Write as if you are explaining the news to a colleague.

Respond with ONLY the summary text — no preamble, no labels, no markdown.
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

def _summarize_one(client, title: str, summary: str, body: str) -> str:
    """Single API call. Uses prompt caching on the system prompt."""
    user_content = f"Title: {title}\n\nSummary: {summary or '(none)'}\n\nBody:\n{body[:MAX_BODY_FOR_LLM]}"
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text.strip()


def summarize_articles(articles: list) -> tuple[list, dict]:
    """
    Generates LLM summaries for a list of Article objects.
    Returns (articles_with_llm_summary, diagnostics).

    Articles where the API call fails keep their original summary.
    If ANTHROPIC_API_KEY is not set, returns articles unchanged with a warning.
    """
    diagnostics = {
        "attempted": 0,
        "cached": 0,
        "generated": 0,
        "failed": 0,
        "skipped_no_key": False,
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        logger.warning("LLM summarization skipped: ANTHROPIC_API_KEY not set")
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

    for article in articles:
        key = _cache_key(article.url)
        if key in cache:
            article.llm_summary = cache[key]["summary"]
            diagnostics["cached"] += 1
            continue

        diagnostics["attempted"] += 1
        if diagnostics["attempted"] > 1:
            time.sleep(_CALL_DELAY_SECONDS)
        try:
            result = _summarize_one(client, article.title, article.summary, article.body)
            article.llm_summary = result
            cache[key] = {"summary": result, "date": today, "url": article.url}
            diagnostics["generated"] += 1
            logger.debug("LLM summary generated for: %r", article.title[:60])
        except Exception as exc:
            logger.warning("LLM summary failed for %r: %s", article.url, exc)
            article.llm_summary = ""
            diagnostics["failed"] += 1

    _save_cache(cache)
    logger.info(
        "LLM summarization: %d generated, %d from cache, %d failed",
        diagnostics["generated"],
        diagnostics["cached"],
        diagnostics["failed"],
    )
    return articles, diagnostics


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


def _find_semantic_duplicates_in_group(client, titles: list) -> list:
    """Single Haiku call: which titles in this list cover the same event?"""
    numbered = "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=[{"type": "text", "text": _DEDUP_SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Identify duplicate events:\n\n{numbered}"}],
        )
        raw = response.content[0].text.strip()
        result = json.loads(raw)
        return result if isinstance(result, list) else []
    except Exception as exc:
        logger.warning("Semantic dedup group call failed: %s", exc)
        return []


def semantic_dedup_articles(articles: list) -> tuple:
    """
    Detect articles covering the same event via LLM title comparison.
    Groups articles by primary company + primary segment, then asks Haiku
    to identify duplicate events within each group.
    Returns (deduplicated_articles, n_merged).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return articles, 0

    try:
        client = _get_client()
    except Exception:
        return articles, 0

    # Group by (primary_company, primary_segment)
    from collections import defaultdict
    groups = defaultdict(list)
    for idx, article in enumerate(articles):
        company = article.companies[0] if article.companies else "__none__"
        segment = article.segments[0] if article.segments else "__none__"
        groups[(company, segment)].append(idx)

    # Only process groups with 2+ articles
    merge_into = {}  # global_idx → global_idx of article to merge into
    call_count = 0
    for (company, segment), indices in groups.items():
        if len(indices) < 2:
            continue
        titles = [articles[i].title for i in indices]
        if call_count > 0:
            time.sleep(_CALL_DELAY_SECONDS)
        dup_groups = _find_semantic_duplicates_in_group(client, titles)
        call_count += 1
        logger.debug(
            "Semantic dedup: %s / %s → %d articles, %d dup groups found",
            company, segment, len(titles), len(dup_groups),
        )
        for dup_group in dup_groups:
            if len(dup_group) < 2:
                continue
            keep_global = indices[dup_group[0] - 1]  # 1-based → 0-based → global
            for local_one_based in dup_group[1:]:
                merge_global = indices[local_one_based - 1]
                if merge_global not in merge_into:
                    merge_into[merge_global] = keep_global

    if not merge_into:
        logger.info("Semantic dedup: no duplicates found across %d groups checked", call_count)
        return articles, 0

    # Apply merges: keep first occurrence, fold duplicates into merged_sources
    n_merged = 0
    result = []
    for idx, article in enumerate(articles):
        if idx in merge_into:
            keep_idx = merge_into[idx]
            kept = articles[keep_idx]
            kept.merged_sources.append(f"{article.source}|||{article.url}")
            n_merged += 1
            logger.info(
                "Semantic dedup merged (%s/%s): %r → %r",
                article.companies[:1], article.segments[:1],
                article.title[:55], kept.title[:55],
            )
        else:
            result.append(article)

    logger.info("Semantic dedup: %d articles merged into existing ones (%d API calls)", n_merged, call_count)
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
