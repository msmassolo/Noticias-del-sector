from collections import Counter
from datetime import datetime, timezone


REGION_ORDER = {"Local": 0, "Regional": 1, "Mundial": 2}
MIN_REGION_QUOTA = {"Local": 7, "Regional": 7, "Mundial": 7}

PRIORITY_COMPANIES = {
    "Red Bull",
    "Monster Beverage",
    "Celsius Holdings",
    "Olipop",
    "Poppi",
    "Fevertree Drinks",
    "AB InBev",
    "Diageo",
    "Campari Group",
    "Constellation Brands",
    "The Coca-Cola Company",
    "PepsiCo",
    "Coca-Cola FEMSA",
    "Arca Continental",
}

TOPIC_WEIGHTS = {
    "financial_results": 32,
    "product_innovation": 28,
    "ma_and_strategy": 26,
    "marketing_innovation": 24,
    "distribution_execution": 22,
    "regulation_tax_policy": 21,
    "risk_crisis_reputation": 20,
    "non_alcoholic_beverages": 19,
    "packaging_sustainability": 18,
    "alternative_ingredients": 17,
    "consumer_market_trends": 16,
    "supply_chain_commodities": 14,
    "company_news": 10,
}

SOURCE_WEIGHTS = {
    "Just Drinks": 18,
    "Beverage Daily": 18,
    "The Drinks Business": 17,
    "Brewbound": 17,
    "The Spirits Business": 17,
    "Wine Business": 16,
    "VinePair": 16,
    "SevenFifty Daily": 15,
    "Financial Times": 16,
    "Bloomberg": 16,
    "Reuters Business": 15,
    "Infobae": 10,
    "La Nacion": 10,
    "Clarin": 9,
    "Ambito": 9,
    "Cronista": 9,
    "Valor Economico": 10,
    "Diario Financiero": 9,
    "Gestion": 8,
    "Portafolio": 8,
}


def _parse_date(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def recency_score(value):
    parsed = _parse_date(value)
    if not parsed:
        return 0
    age_days = max((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).days, 0)
    if age_days <= 1:
        return 12
    if age_days <= 3:
        return 8
    if age_days <= 7:
        return 4
    return 0


def score_item(item):
    candidate = item["candidate"]
    score = 0
    score += SOURCE_WEIGHTS.get(candidate.source, 4)
    score += recency_score(candidate.published)

    topics = item.get("segments") or []
    if topics:
        score += max(TOPIC_WEIGHTS.get(topic, 0) for topic in topics)
        score += min(len(topics), 3) * 3

    companies = set(item.get("companies") or [])
    if companies:
        score += 28
        score += sum(10 for company in companies if company in PRIORITY_COMPANIES)

    if item.get("reason") == "company_match":
        score += 8
    elif item.get("reason") == "strong_keyword_match":
        score += 5
    elif item.get("reason") == "local_regional_beverage_section":
        score += 3

    if candidate.discovery.startswith("google_news"):
        # Google News redirect URLs fail extraction; deprioritize vs direct sources
        score -= 20

    return score


def rank_items(items):
    for item in items:
        item["score"] = score_item(item)
    return sorted(
        items,
        key=lambda item: (
            -item["score"],
            REGION_ORDER.get(item["candidate"].region, 9),
            item["candidate"].published or "",
            item["candidate"].title,
        ),
    )


def select_balanced_articles(articles, target_count=40, min_per_region=7):
    by_region = {"Local": [], "Regional": [], "Mundial": []}
    for article in articles:
        by_region.setdefault(article.region or "Mundial", []).append(article)

    selected = []
    used_urls = set()

    for region in ("Local", "Regional", "Mundial"):
        quota = min_per_region if isinstance(min_per_region, int) else MIN_REGION_QUOTA.get(region, 7)
        for article in by_region.get(region, [])[:quota]:
            if article.url not in used_urls:
                selected.append(article)
                used_urls.add(article.url)

    for article in articles:
        if len(selected) >= target_count:
            break
        if article.url not in used_urls:
            selected.append(article)
            used_urls.add(article.url)

    counts = Counter(article.region or "Mundial" for article in selected)
    available = Counter(article.region or "Mundial" for article in articles)
    return selected[:target_count], {"selected_by_region": dict(counts), "available_by_region": dict(available)}
