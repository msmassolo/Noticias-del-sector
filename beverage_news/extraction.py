import json
import logging

from bs4 import BeautifulSoup

from .http import fetch_text
from .models import Article
from .text import clean_text, natural_trim

logger = logging.getLogger(__name__)


MAX_BODY_CHARS = 12000
BOILERPLATE_PATTERNS = (
    "combine business intelligence and editorial excellence",
    "stay ahead with unbiased news",
    "as a trusted provider of data and insights",
    "gain a deeper understanding",
    "already a subscriber",
    "please complete this form",
    "experience unmatched clarity",
)

def _iter_json_ld(data):
    if isinstance(data, dict):
        yield data
        for value in data.values():
            yield from _iter_json_ld(value)
    elif isinstance(data, list):
        for item in data:
            yield from _iter_json_ld(item)


def _json_ld_articles(soup):
    articles = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            parsed = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue

        for item in _iter_json_ld(parsed):
            item_type = item.get("@type")
            types = item_type if isinstance(item_type, list) else [item_type]
            if any(str(value).lower() in {"newsarticle", "article", "reportagenewsarticle"} for value in types):
                articles.append(item)
    return articles


def _meta_content(soup, *names):
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find("meta", attrs={"property": name})
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return ""


def _is_boilerplate(text):
    lowered = text.lower()
    return any(pattern in lowered for pattern in BOILERPLATE_PATTERNS)



def extract_title_summary_body(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    json_articles = _json_ld_articles(soup)

    title = ""
    summary = ""
    body = ""
    published = ""

    for article in json_articles:
        title = title or clean_text(article.get("headline") or article.get("name") or "")
        summary = summary or clean_text(article.get("description") or "")
        body = body or clean_text(article.get("articleBody") or "")
        published = published or clean_text(article.get("datePublished") or article.get("dateModified") or "")

    title = title or _meta_content(soup, "og:title", "twitter:title") or clean_text(soup.title.get_text(" ") if soup.title else "")
    summary = summary or _meta_content(soup, "description", "og:description", "twitter:description")
    published = published or _meta_content(soup, "article:published_time", "date")

    if not body:
        container = soup.find("article") or soup.find("main") or soup
        paragraphs = []
        seen = set()
        for tag in container.find_all("p"):
            text = clean_text(tag.get_text(" "))
            key = text.lower()
            if len(text) >= 55 and key not in seen and not _is_boilerplate(text):
                paragraphs.append(text)
                seen.add(key)
        body = "\n\n".join(paragraphs)

    return {
        "title": title,
        "summary": summary,
        "body": natural_trim(body, MAX_BODY_CHARS),
        "published": published,
    }


def extract_article_item(item, min_body_chars=80):
    candidate = item["candidate"]
    html_text, status = fetch_text(candidate.url, timeout=10, retries=1)

    body = ""
    extracted = {}
    if html_text:
        extracted = extract_title_summary_body(html_text)
        body = extracted.get("body", "")

    if len(body) < min_body_chars:
        # Fall back to the RSS/section summary if available and long enough
        fallback = (candidate.summary or "").strip()
        if len(fallback) >= min_body_chars:
            body = fallback
        else:
            reason = status if not html_text else "body_too_short"
            logger.debug("Extraction skipped: %s — %s", reason, candidate.url)
            return None, {"url": candidate.url, "reason": reason}

    from .validation import GENERIC_TITLES
    extracted_title = extracted.get("title", "").strip()
    lowered = extracted_title.lower()
    title_is_generic = not extracted_title or any(
        lowered == g or lowered.startswith(g + " -") or lowered.startswith(g + " |")
        for g in GENERIC_TITLES
    )
    title = candidate.title if title_is_generic else extracted_title

    article = Article(
        title=title,
        url=candidate.url,
        source=candidate.source,
        country=candidate.country,
        region=candidate.region,
        language=candidate.language,
        published=extracted["published"] or candidate.published,
        summary=extracted["summary"] or candidate.summary,
        body=body,
        companies=item["companies"],
        segments=item["segments"],
        keyword_categories=item["keyword_categories"],
        discovery=candidate.discovery,
    )
    return article, None


def extract_articles(accepted_items):
    diagnostics = {"attempted": 0, "extracted": 0, "errors": []}
    articles = []

    for item in accepted_items:
        diagnostics["attempted"] += 1
        article, error = extract_article_item(item)
        if error:
            diagnostics["errors"].append(error)
            continue
        articles.append(article)
        diagnostics["extracted"] += 1

    return articles, diagnostics
