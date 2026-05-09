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
from .ranking import rank_items, select_balanced_articles
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


def _extract_ranked_selection(extraction_queue, target_count, min_per_region, min_body_chars=80):
    diagnostics = {"attempted": 0, "extracted": 0, "errors": []}
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
            article, error = future.result()
            if error:
                diagnostics["errors"].append(error)
                continue
            articles.append(article)
            diagnostics["extracted"] += 1

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

    # Direct-source URLs come before Google News redirects (GN URLs already penalized -20 in ranking)
    direct = [item for item in accepted if "news.google.com" not in item["candidate"].url]
    gn = [item for item in accepted if "news.google.com" in item["candidate"].url]
    extraction_queue = (direct + gn)[:limit]
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

    logger.info("Pipeline: extraction queue has %d items (direct=%d, gn=%d)", len(extraction_queue), len(direct), len(gn))
    extracted_articles, extraction_diagnostics = _extract_ranked_selection(extraction_queue, target_count, min_per_region, min_body_chars)
    logger.info("Pipeline: extracted %d / %d articles", extraction_diagnostics["extracted"], extraction_diagnostics["attempted"])
    extracted_articles, validation_diagnostics = validate_articles(extracted_articles)
    articles, selection_diagnostics = select_balanced_articles(
        extracted_articles,
        target_count=target_count,
        min_per_region=min_per_region,
    )
    _write_json("articles.json", [asdict(article) for article in articles])

    diagnostics = {
        "discovery": discovery_diagnostics,
        "filtering": filtering_diagnostics,
        "extraction": extraction_diagnostics,
        "validation": validation_diagnostics,
        "selection": selection_diagnostics,
    }
    _write_json("diagnostics.json", diagnostics)

    _update_published_urls(articles)
    generate_web(articles, diagnostics=diagnostics, output_path=output_path)
    logger.info("Pipeline: done — %d articles published to %s", len(articles), output_path)
    return articles, diagnostics
