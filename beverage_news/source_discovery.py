"""
Auto-discovery of new sources.

After each pipeline run, this module checks which article URLs came from
domains NOT already in sources.json. Domains that appear frequently are
saved to data/discovered_sources.json for manual review.

The user reviews that file and can promote promising domains to sources.json.
Nothing is auto-added — this is a suggestion box, not auto-configuration.
"""

import json
import logging
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

DISCOVERED_FILE = Path("data/discovered_sources.json")
MIN_APPEARANCES = 2  # Domain must appear this many times to be suggested


def _domain(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def _known_domains(sources_path="config/sources.json") -> set:
    try:
        with open(sources_path, "r", encoding="utf-8") as f:
            sources = json.load(f)
        domains = set()
        for s in sources:
            d = _domain(s.get("url", ""))
            if d:
                domains.add(d)
        return domains
    except Exception:
        return set()


def _load_discovered() -> dict:
    if not DISCOVERED_FILE.exists():
        return {}
    try:
        with open(DISCOVERED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_discovered(data: dict) -> None:
    DISCOVERED_FILE.parent.mkdir(exist_ok=True)
    with open(DISCOVERED_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_and_suggest(articles: list, sources_path="config/sources.json") -> list:
    """
    Check articles for domains not in sources.json.
    Update discovered_sources.json with cumulative counts.
    Return list of new suggested domains (those crossing MIN_APPEARANCES).

    Call this after the final article list is built.
    """
    known = _known_domains(sources_path)

    # Collect domains from this run
    run_domains = Counter()
    example_urls = {}
    for article in articles:
        d = _domain(article.url)
        if d and d not in known and "google.com" not in d and "facebook.com" not in d:
            run_domains[d] += 1
            if d not in example_urls:
                example_urls[d] = article.url

    if not run_domains:
        return []

    # Merge with historical data
    discovered = _load_discovered()
    new_suggestions = []
    for domain, count in run_domains.items():
        entry = discovered.get(domain, {"count": 0, "example_url": "", "suggested": False})
        was_below = entry["count"] < MIN_APPEARANCES
        entry["count"] += count
        entry["example_url"] = example_urls.get(domain, entry.get("example_url", ""))
        if was_below and entry["count"] >= MIN_APPEARANCES and not entry.get("suggested"):
            entry["suggested"] = True
            new_suggestions.append(domain)
        discovered[domain] = entry

    _save_discovered(discovered)

    if new_suggestions:
        logger.info(
            "Source discovery: %d new domains cross the threshold and appear in "
            "data/discovered_sources.json for review: %s",
            len(new_suggestions), new_suggestions,
        )

    return new_suggestions
