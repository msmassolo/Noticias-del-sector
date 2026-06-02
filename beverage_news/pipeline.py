import json
import logging
from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

from .config import load_companies, load_keywords, load_sources
from .discovery import discover_candidates
from .extraction import extract_article_item
from .filtering import filter_candidates
from .llm import summarize_articles, review_dashboard
from .ranking import build_extraction_queue, rank_items, select_balanced_articles
from .validation import validate_articles
from .web import generate_web


DATA_DIR = Path("data")
PUBLISHED_URLS_FILE = Path("published_urls.json")
PUBLISHED_URLS_TTL_DAYS = 7


def _load_published_urls():
    if not PUBLISHED_URLS_FILE.exists():
        return {}
    with open(PUBLISHED_URLS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_published_urls(url_map):
    DATA_DIR.mkdir(exist_ok=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=PUBLISHED_URLS_TTL_DAYS)).isoformat()
    pruned = {key: ts for key, ts in url_map.items() if ts >= cutoff}
    with open(PUBLISHED_URLS_FILE, "w", encoding="utf-8") as f:
        json.dump(pruned, f, ensure_ascii=False, indent=2)


def _update_published_urls(articles):
    from .text import normalize_text
    url_map = _load_published_urls()
    now = datetime.now(timezone.utc).isoformat()
    for article in articles:
        url_map[article.url] = now
        title_key = f"title:{normalize_text(article.title)}"
        url_map[title_key] = now
    _save_published_urls(url_map)


def _write_json(name, data):
    DATA_DIR.mkdir(exist_ok=True)
    with open(DATA_DIR / name, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _merge_accepted_candidates(primary, fallback):
    merged = []
    seen = set()
    for item in [*primary, *fallback]:
        url = item["candidate"].url
        title = item["candidate"].title
        key = (url, title)
        if key in seen:
            continue
        seen.add(key)
        merged.append(item)
    return merged


_TITLE_STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "en", "y", "a", "que", "se",
    "con", "por", "para", "un", "una", "al", "su", "the", "a", "an", "of",
    "in", "and", "to", "for", "on", "is", "are", "with", "at", "by", "its",
}


def _title_tokens(title):
    from .text import normalize_text
    words = normalize_text(title).split()
    return {w for w in words if len(w) > 2 and w not in _TITLE_STOPWORDS}


def _dedup_similar_titles(articles, threshold=0.60):
    """Remove articles whose title shares ≥60% token overlap with a higher-ranked article."""
    kept = []
    for article in articles:
        tokens = _title_tokens(article.title)
        is_dup = False
        for ref in kept:
            ref_tokens = _title_tokens(ref.title)
            union = tokens | ref_tokens
            if not union:
                continue
            jaccard = len(tokens & ref_tokens) / len(union)
            if jaccard >= threshold:
                is_dup = True
                logger.info("Dedup similar titles (%.0f%%): %r vs %r", jaccard * 100, article.title[:60], ref.title[:60])
                break
        if not is_dup:
            kept.append(article)
    return kept


def _extract_ranked_selection(extraction_queue, target_count, min_per_region, min_body_chars=80):
    diagnostics = {"attempted": 0, "extracted": 0, "errors": [], "by_source": {}, "by_region": {}}
    articles = []
    item_by_url = {}
    deduped_queue = []
    for item in extraction_queue:
        url = item["candidate"].url
        if url not in item_by_url:
            item_by_url[url] = item
            deduped_queue.append(item)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(extract_article_item, item, min_body_chars): item for item in deduped_queue}
        diagnostics["attempted"] = len(futures)
        for future in as_completed(futures):
            item = futures[future]
            candidate = item["candidate"]
            source_stats = diagnostics["by_source"].setdefault(candidate.source, {"attempted": 0, "extracted": 0, "errors": {}})
            region_stats = diagnostics["by_region"].setdefault(candidate.region or "Mundial", {"attempted": 0, "extracted": 0, "errors": {}})
            source_stats["attempted"] += 1
            region_stats["attempted"] += 1
            article, error = future.result()
            if error:
                diagnostics["errors"].append(error)
                reason = error.get("reason", "unknown")
                source_stats["errors"][reason] = source_stats["errors"].get(reason, 0) + 1
                region_stats["errors"][reason] = region_stats["errors"].get(reason, 0) + 1
                continue
            articles.append(article)
            diagnostics["extracted"] += 1
            source_stats["extracted"] += 1
            region_stats["extracted"] += 1

    score_by_url = {item["candidate"].url: item.get("score", 0) for item in deduped_queue}
    articles.sort(key=lambda article: -score_by_url.get(article.url, 0))
    return articles, diagnostics


def run_pipeline(
    output_path="index.html",
    enable_search=True,
    limit=80,
    target_count=40,
    min_per_region=7,
    max_search_queries=55,
    min_body_chars=80,
):
    sources = load_sources()
    companies = load_companies()
    keywords = load_keywords()
    logger.info("Pipeline: loaded %d sources, %d companies, %d keyword categories", len(sources), len(companies), len(keywords))

    candidates, discovery_diagnostics = discover_candidates(
        companies,
        keywords,
        sources,
        enable_search=enable_search,
        max_search_queries=max_search_queries,
    )
    _write_json("candidates.json", [asdict(candidate) for candidate in candidates])

    published_urls = _load_published_urls()
    accepted, filtering_diagnostics = filter_candidates(candidates, companies, keywords, published_urls)
    accepted = rank_items(accepted)

    recycled_accepted = []
    recycled_filtering_diagnostics = None
    recycle_threshold = max(limit, target_count * 3)
    if len(accepted) < recycle_threshold and published_urls:
        recycled_accepted, recycled_filtering_diagnostics = filter_candidates(candidates, companies, keywords, published_urls={})
        recycled_accepted = rank_items(recycled_accepted)
        accepted = rank_items(_merge_accepted_candidates(accepted, recycled_accepted))

    extraction_queue, queue_diagnostics = build_extraction_queue(
        accepted,
        limit=limit,
        target_count=target_count,
        min_per_region=min_per_region,
    )
    _write_json(
        "accepted_candidates.json",
        [
            {
                "candidate": asdict(item["candidate"]),
                "companies": item["companies"],
                "segments": item["segments"],
                "keyword_categories": item["keyword_categories"],
                "reason": item["reason"],
                "score": item.get("score", 0),
            }
            for item in extraction_queue
        ],
    )

    logger.info("Pipeline: extraction queue has %d items", len(extraction_queue))
    extracted_articles, extraction_diagnostics = _extract_ranked_selection(extraction_queue, target_count, min_per_region, min_body_chars)
    logger.info("Pipeline: extracted %d / %d articles", extraction_diagnostics["extracted"], extraction_diagnostics["attempted"])
    extracted_articles, validation_diagnostics = validate_articles(extracted_articles)
    articles, selection_diagnostics = select_balanced_articles(
        extracted_articles,
        target_count=target_count,
        min_per_region=min_per_region,
    )
    articles = _dedup_similar_titles(articles)
    _write_json("articles.json", [asdict(article) for article in articles])

    articles, llm_diagnostics = summarize_articles(articles)
    qa_result = review_dashboard(articles)

    diagnostics = {
        "discovery": discovery_diagnostics,
        "filtering": filtering_diagnostics,
        "recycled_filtering": recycled_filtering_diagnostics,
        "queue": queue_diagnostics,
        "extraction": extraction_diagnostics,
        "validation": validation_diagnostics,
        "selection": selection_diagnostics,
        "llm": llm_diagnostics,
        "qa": qa_result,
    }
    _write_json("diagnostics.json", diagnostics)

    _update_published_urls(articles)
    generate_web(articles, diagnostics=diagnostics, output_path=output_path, qa=qa_result)
    logger.info("Pipeline: done — %d articles published to %s", len(articles), output_path)
    return articles, diagnostics
