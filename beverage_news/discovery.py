import email.utils
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .http import fetch_text, SESSION
from .models import Candidate
from .text import clean_text
from .urls import domain_of, normalize_url

logger = logging.getLogger(__name__)


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
GOOGLE_CSE_URL = "https://www.googleapis.com/customsearch/v1"

# Free tier: 100 queries/day. We cap at 95 to leave 5 of safety buffer.
_CSE_DAILY_LIMIT = 95
_CSE_USAGE_FILE = Path("data/google_cse_usage.json")


def _cse_load_usage() -> dict:
    if not _CSE_USAGE_FILE.exists():
        return {"date": "", "count": 0}
    try:
        with open(_CSE_USAGE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"date": "", "count": 0}


def _cse_save_usage(count: int) -> None:
    _CSE_USAGE_FILE.parent.mkdir(exist_ok=True)
    today = datetime.now(timezone.utc).date().isoformat()
    with open(_CSE_USAGE_FILE, "w", encoding="utf-8") as f:
        json.dump({"date": today, "count": count}, f)


def _cse_remaining_quota() -> int:
    """Returns how many CSE queries we can still make today."""
    today = datetime.now(timezone.utc).date().isoformat()
    usage = _cse_load_usage()
    if usage.get("date") != today:
        return _CSE_DAILY_LIMIT  # New day, full quota
    return max(0, _CSE_DAILY_LIMIT - usage.get("count", 0))
REGION_LOCALES = {
    "local": ("es-419", "AR", "AR:es-419"),
    "regional": ("es-419", "CL", "CL:es-419"),
    "regional_pt": ("pt-BR", "BR", "BR:pt-419"),
    "mundial": ("en-US", "US", "US:en"),
}


def classify_region(source, url):
    lowered = (url or "").lower()
    if source.region == "Local":
        if any(part in lowered for part in ("/mexico/", "/peru/", "/colombia/", "/chile/", "/uruguay/", "/america/")):
            return "Regional"
        if any(part in lowered for part in ("/espana/", "/eeuu/", "/estados-unidos/", "/mundo/")):
            return "Mundial"
    return source.region


def _xml_text(node, child_name):
    child = node.find(child_name)
    if child is not None and child.text:
        return clean_text(child.text)
    return ""


def _xml_raw(node, child_name):
    child = node.find(child_name)
    if child is not None and child.text:
        return child.text
    return ""


_REL_AGO_RE = re.compile(r"\b(\d{1,3})\s*(minutes?|hours?|days?)\s+ago\b", re.IGNORECASE)
_REL_HACE_RE = re.compile(
    r"\bhace\s+(\d{1,3})\s*(minutos?|min|horas?|hr|h|d[ií]as?)\b",
    re.IGNORECASE,
)
_REL_KEYWORDS = (
    ("anteayer", timedelta(days=2)),
    ("ayer", timedelta(days=1)),
    ("yesterday", timedelta(days=1)),
    ("hace instantes", timedelta(minutes=5)),
    ("just now", timedelta(minutes=0)),
    ("esta manana", timedelta(hours=4)),
    ("esta mañana", timedelta(hours=4)),
    ("esta tarde", timedelta(hours=2)),
    ("hoy", timedelta(hours=0)),
    ("today", timedelta(hours=0)),
)


def _parse_relative_date(value):
    lowered = value.lower().strip()
    for kw, delta in _REL_KEYWORDS:
        if kw in lowered:
            return (datetime.now(timezone.utc) - delta).isoformat()
    m = _REL_AGO_RE.search(lowered)
    if m:
        qty = int(m.group(1))
        unit = m.group(2).lower()
        delta = timedelta(minutes=qty) if unit.startswith("minute") else (
            timedelta(hours=qty) if unit.startswith("hour") else timedelta(days=qty)
        )
        return (datetime.now(timezone.utc) - delta).isoformat()
    m = _REL_HACE_RE.search(lowered)
    if m:
        qty = int(m.group(1))
        unit = m.group(2).lower()
        if unit.startswith("min"):
            delta = timedelta(minutes=qty)
        elif unit.startswith(("hor", "hr", "h")):
            delta = timedelta(hours=qty)
        else:
            delta = timedelta(days=qty)
        return (datetime.now(timezone.utc) - delta).isoformat()
    return None


def _parse_date(value):
    if not value:
        return ""
    try:
        return email.utils.parsedate_to_datetime(value).isoformat()
    except Exception:
        pass
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except Exception:
        pass
    rel = _parse_relative_date(value)
    if rel:
        return rel
    return clean_text(value)


def parse_rss(xml_text, source):
    root = ET.fromstring(xml_text)
    candidates = []

    for item in root.findall(".//item"):
        title = _xml_text(item, "title")
        link = _xml_text(item, "link")
        description = clean_text(BeautifulSoup(_xml_text(item, "description"), "html.parser").get_text(" "))
        published = _parse_date(_xml_text(item, "pubDate"))
        url = normalize_url(source.url, link)
        if title and url:
            candidates.append(
                Candidate(
                    title=title,
                    url=url,
                    source=source.name,
                    source_url=source.url,
                    country=source.country,
                    region=classify_region(source, url),
                    language=source.language,
                    published=published,
                    summary=description,
                    discovery="rss",
                    trade_source=source.trade,
                    require_section=source.require_section,
                )
            )

    return candidates


def _title_from_link(link_tag):
    candidates = [
        link_tag.get("aria-label", ""),
        link_tag.get("title", ""),
        link_tag.get_text(" ", strip=True),
    ]
    for selector in ("h1", "h2", "h3", "h4"):
        heading = link_tag.find(selector)
        if heading:
            candidates.append(heading.get_text(" ", strip=True))
            candidates.append(heading.get("aria-label", ""))
            candidates.append(heading.get("title", ""))
    cleaned = [clean_text(item) for item in candidates if clean_text(item)]
    if not cleaned:
        return ""
    return max(cleaned, key=lambda item: (len(item), item.count(" ")))


def discover_from_sections(sources, diagnostics):
    candidates = []

    def fetch_section(source, section_url):
        html_text, status = fetch_text(section_url, timeout=3)
        return source, section_url, html_text, status

    tasks = []
    with ThreadPoolExecutor(max_workers=10) as executor:
        for source in sources:
            for section_url in source.sections:
                tasks.append(executor.submit(fetch_section, source, section_url))

        for task in as_completed(tasks):
            source, section_url, html_text, status = task.result()
            if not html_text:
                diagnostics["section_errors"].append({"source": source.name, "url": section_url, "reason": status})
                continue

            try:
                soup = BeautifulSoup(html_text, "html.parser")
                section_candidates = []
                seen = set()
                for link_tag in soup.find_all("a"):
                    title = _title_from_link(link_tag)
                    href = link_tag.get("href") or ""
                    url = normalize_url(source.url, href)
                    if not title or not url or url in seen:
                        continue
                    if len(title) < 28:
                        continue
                    if any(part in url for part in ("/tag/", "/autor/", "/authors/", "#")):
                        continue
                    seen.add(url)
                    section_candidates.append(
                        Candidate(
                            title=title,
                            url=url,
                            source=source.name,
                            source_url=source.url,
                            country=source.country,
                            region=classify_region(source, url),
                            language=source.language,
                            discovery=f"section:{section_url}",
                            trade_source=source.trade,
                            require_section=source.require_section,
                        )
                    )
                diagnostics["section_counts"][section_url] = len(section_candidates)
                candidates.extend(section_candidates[:24])
            except Exception as exc:
                diagnostics["section_errors"].append(
                    {"source": source.name, "url": section_url, "reason": f"section_parse_error:{exc.__class__.__name__}"}
                )
    return candidates


def discover_from_rss(sources, diagnostics):
    candidates = []
    for source in sources:
        rss_urls = list(source.rss)
        discovered = [] if source.sections and not source.rss else discover_feed_urls(source)
        rss_urls.extend(url for url in discovered if url not in rss_urls)
        if discovered:
            diagnostics["discovered_feeds"][source.name] = discovered

        for rss_url in rss_urls:
            xml_text, status = fetch_text(rss_url)
            if not xml_text:
                diagnostics["source_errors"].append({"source": source.name, "url": rss_url, "reason": status})
                continue
            try:
                parsed = parse_rss(xml_text, source)
                diagnostics["source_counts"][source.name] = diagnostics["source_counts"].get(source.name, 0) + len(parsed)
                candidates.extend(parsed)
            except ET.ParseError:
                diagnostics["source_errors"].append({"source": source.name, "url": rss_url, "reason": "rss_parse_error"})
    return candidates


def discover_feed_urls(source):
    html_text, status = fetch_text(source.url)
    if not html_text:
        return []

    soup = BeautifulSoup(html_text, "html.parser")
    feeds = []
    for link in soup.find_all("link", rel=True):
        rel = " ".join(link.get("rel") or []).lower()
        feed_type = (link.get("type") or "").lower()
        href = link.get("href") or ""
        if "alternate" not in rel:
            continue
        if "rss" not in feed_type and "atom" not in feed_type and "xml" not in feed_type:
            continue
        full_url = normalize_url(source.url, href)
        lowered = full_url.lower()
        if not full_url:
            continue
        if "oembed" in lowered or "/comments/feed" in lowered:
            continue
        if full_url:
            feeds.append(full_url)

    return list(dict.fromkeys(feeds))


def _google_news_url(query, region_key="mundial"):
    locale = REGION_LOCALES.get(region_key, REGION_LOCALES["mundial"])
    hl, gl, ceid = locale
    return GOOGLE_NEWS_RSS.format(query=quote_plus(query), hl=hl, gl=gl, ceid=ceid)


def _original_url_from_google_description(description_html):
    soup = BeautifulSoup(description_html or "", "html.parser")
    for link in soup.find_all("a", href=True):
        href = normalize_url("", link.get("href") or "")
        if href and "news.google.com" not in domain_of(href):
            return href
    return ""


def discover_from_google_news(companies, keywords, sources, diagnostics, max_queries=55):
    source_by_domain = {domain_of(source.url): source for source in sources}
    global_queries = []

    for company in companies:
        global_queries.append(f'"{company.name}" beverage OR beer OR drinks OR spirits OR soda')

    strategic_terms = [
        "beverage industry",
        "beer brewer acquisition",
        "soft drinks sugar tax",
        "spirits earnings",
        "bottled water market",
        "energy drinks regulation",
        "beverage ingredient innovation",
        "functional beverage ingredient",
        "cold chain beverage logistics",
        "non-alcoholic spirits market",
        "beverage contract manufacturing",
        "alcohol-free beverage trend",
        "beverage packaging sustainability",
        "kombucha kefir fermented beverage",
        "cerveza cervecera resultados",
        "bebidas gaseosas impuesto",
        "bebidas energéticas regulación",
        "bebidas aquisição cerveja",
    ]
    global_queries.extend(strategic_terms)
    global_queries = global_queries[:max_queries]

    local_queries = [
        '"Coca-Cola" Argentina bebidas',
        '"PepsiCo" Argentina bebidas',
        '"Quilmes" cerveza Argentina',
        '"AB InBev" Argentina Quilmes',
        '"Cerveceria y Malteria Quilmes"',
        '"Diageo" Argentina',
        '"Campari" Argentina',
        '"Grupo Cepas" OR "Gancia" OR "Terma" Argentina',
        '"Refres Now" OR "Manaos" Argentina bebidas',
        '"Villavicencio" OR "Villa del Sur" Argentina',
        '"bebidas" Argentina resultados',
        '"bebidas" Argentina novedades',
        '"bebidas" Argentina consumo tendencia',
        '"gaseosas" Argentina consumo',
        '"cerveza" Argentina ventas',
        '"cerveza artesanal" Argentina',
        '"energizantes" Argentina bebidas',
        '"bebidas sin alcohol" Argentina',
        '"bebidas sin alcohol" Argentina tendencia',
        '"vino" Argentina exportaciones',
        '"vino" Argentina bodegas',
        '"mosto" Argentina exportacion',
        '"mosto de uva" Argentina',
        '"vitivinicola" Argentina',
        '"bodega" Argentina resultados',
        '"industria bebidas" Argentina',
        '"alimentos y bebidas" Argentina evento',
        '"consumo bebidas" Argentina precios',
        '"bebidas" Mendoza Cordoba Tucuman',
    ]
    regional_queries = [
        # Empresas regionales clave
        '"Coca-Cola FEMSA" bebidas',
        '"Arca Continental" bebidas',
        '"Ambev" resultados',
        '"Coca-Cola Andina" bebidas',
        '"Embotelladora Andina"',
        '"CCU" cerveza bebidas',
        '"Concha y Toro" resultados',
        '"Concha y Toro" vino',
        '"Aje Group" bebidas',
        '"Postobon" bebidas',
        '"Backus" cerveza Peru',
        '"Bavaria" cerveza Colombia',
        '"Quilmes" Brasil OR Chile OR Peru OR Colombia',
        '"Heineken" Brasil OR Chile OR Mexico',
        '"Diageo" Brasil OR Chile OR Colombia',
        '"Campari" Brasil OR Chile',
        '"Pernod Ricard" Brasil OR Chile OR Colombia',
        # Categorías por país
        '"bebidas" Brasil resultados',
        '"cerveja" Brasil resultados',
        '"bebidas" Chile resultados mercado',
        '"bebidas" Peru mercado',
        '"bebidas" Colombia mercado',
        '"bebidas" Mexico resultados',
        '"cerveza" Mexico resultados',
        '"vino" Chile exportaciones',
        '"vino" Chile resultados',
        '"pisco" Chile Peru',
        '"ron" Colombia Venezuela Caribbean',
        '"industria bebidas" Brasil',
        '"industria bebidas" Chile',
        '"industria bebidas" Colombia',
        '"bebidas" Sudamerica tendencia',
        '"bebidas sin alcohol" Brasil OR Chile OR Colombia',
        '"energia" bebida energizante Brasil OR Chile',
        '"agua" embotellada Brasil OR Chile resultados',
        '"cerveza artesanal" Brasil OR Chile OR Colombia',
        '"mosto" Chile OR Brasil exportacion',
        '"vitivinicola" Chile resultados',
        '"vino" Brasil mercado tendencia',
        '"bebidas" LATAM OR "America Latina" tendencias',
    ]
    query_groups = [
        ("Mundial", "mundial", global_queries),
        ("Local", "local", local_queries),
        ("Regional", "regional", regional_queries),
        ("Regional", "regional_pt", regional_queries),
    ]

    candidates = []
    search_queries = []
    consecutive_timeouts = 0
    MAX_CONSECUTIVE_TIMEOUTS = 3  # Abort early if Google is blocking (e.g. CI IPs)

    for region, region_key, queries in query_groups:
        if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
            logger.warning(
                "Google News: %d consecutive timeouts — aborting search discovery (likely IP-blocked)",
                consecutive_timeouts,
            )
            break
        for query in queries:
            if consecutive_timeouts >= MAX_CONSECUTIVE_TIMEOUTS:
                break
            search_queries.append({"region": region, "query": query})
            rss_url = _google_news_url(query, region_key)
            # Use short timeout + no retries: if Google is blocked, fail fast (6s max vs 15s with retries)
            xml_text, status = fetch_text(rss_url, timeout=6, retries=0)
            if not xml_text:
                if status == "timeout":
                    consecutive_timeouts += 1
                else:
                    consecutive_timeouts = 0  # non-timeout error, don't count toward abort
                diagnostics["search_errors"].append({"region": region, "query": query, "reason": status})
                continue
            consecutive_timeouts = 0
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError:
                diagnostics["search_errors"].append({"region": region, "query": query, "reason": "rss_parse_error"})
                continue

            for item in root.findall(".//item"):
                title = _xml_text(item, "title")
                google_link = normalize_url("", _xml_text(item, "link"))
                description_raw = _xml_raw(item, "description")
                original_link = _original_url_from_google_description(description_raw)
                link = original_link or google_link
                description = clean_text(BeautifulSoup(description_raw, "html.parser").get_text(" "))
                published = _parse_date(_xml_text(item, "pubDate"))
                if not title or not link:
                    continue

                domain = domain_of(link)
                matched_source = next((source for source_domain, source in source_by_domain.items() if source_domain in domain), None)
                candidates.append(
                    Candidate(
                        title=title,
                        url=link,
                        source=matched_source.name if matched_source else domain or "Google News",
                        source_url=matched_source.url if matched_source else "",
                        country=matched_source.country if matched_source else ("Argentina" if region == "Local" else region),
                        region=matched_source.region if matched_source else region,
                        language=matched_source.language if matched_source else "",
                        published=published,
                        summary=description,
                        discovery=f"google_news:{query}",
                        trade_source=matched_source.trade if matched_source else False,
                        require_section=matched_source.require_section if matched_source else False,
                    )
                )

    diagnostics["search_queries"] = search_queries
    return candidates


def discover_from_google_cse(companies, keywords, sources, diagnostics, max_queries=50):
    """
    Discover candidates via Google Custom Search JSON API.
    Requires GOOGLE_API_KEY and GOOGLE_CSE_ID env vars.
    Free tier: 100 queries/day. Hard cap at 95 to leave safety buffer.
    Returns (candidates, quota_exhausted: bool).
    """
    api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
    if not api_key or not cse_id:
        logger.warning("Google CSE: GOOGLE_API_KEY or GOOGLE_CSE_ID not set — skipping CSE discovery")
        return [], False

    remaining = _cse_remaining_quota()
    if remaining == 0:
        logger.warning("Google CSE: daily quota exhausted (limit=%d) — skipping CSE, will use Google News RSS", _CSE_DAILY_LIMIT)
        return [], True  # Signal caller to fall back

    source_by_domain = {domain_of(source.url): source for source in sources}

    # High-priority queries: local Argentine + key companies + strategic
    queries = []

    # Local Argentine queries (highest priority — weakest coverage from RSS)
    local_queries = [
        ('"bebidas" Argentina resultados OR lanzamiento OR estrategia', "Local"),
        ('"cerveza" Argentina noticias OR novedades', "Local"),
        ('"vino" Argentina exportaciones OR bodega OR resultados', "Local"),
        ('"mosto" OR "mosto concentrado" Argentina exportacion', "Local"),
        ('"Grupo Cepas" OR "Gancia" OR "Terma" Argentina', "Local"),
        ('"Quilmes" cerveza Argentina', "Local"),
        ('"Coca-Cola" OR "PepsiCo" Argentina bebidas', "Local"),
        ('"gaseosas" OR "bebidas sin alcohol" Argentina consumo', "Local"),
        ('"distribucion" bebidas Argentina canal', "Local"),
        ('"vitivinicola" OR "bodega" Argentina novedades', "Local"),
    ]
    queries.extend(local_queries)

    # Key company queries (top global players)
    priority_companies = [
        "AB InBev", "Diageo", "Pernod Ricard", "Heineken", "Campari Group",
        "The Coca-Cola Company", "PepsiCo", "Constellation Brands", "Monster Beverage",
        "Red Bull", "Coca-Cola FEMSA", "Ambev",
    ]
    for company_name in priority_companies:
        queries.append((f'"{company_name}" beverage news results acquisition', "Mundial"))

    # Strategic global terms
    strategic = [
        ("beverage industry M&A acquisition 2026", "Mundial"),
        ("beer spirits wine earnings results 2026", "Mundial"),
        ("beverage innovation launch functional drink", "Mundial"),
        ("alcohol regulation sugar tax beverage policy", "Mundial"),
        ("bebidas LATAM resultados lanzamiento", "Regional"),
        ("cerveza vino LATAM tendencias mercado", "Regional"),
    ]
    queries.extend(strategic)

    # Respect both the configured max and the remaining daily quota
    effective_max = min(max_queries, remaining)
    queries = queries[:effective_max]

    today = datetime.now(timezone.utc).date().isoformat()
    usage = _cse_load_usage()
    used_count = usage.get("count", 0) if usage.get("date") == today else 0

    candidates = []
    errors = 0
    quota_exhausted = False

    for query_text, region in queries:
        try:
            params = {
                "key": api_key,
                "cx": cse_id,
                "q": query_text,
                "num": 10,
                "dateRestrict": "d2",
                "sort": "date",
            }
            resp = SESSION.get(GOOGLE_CSE_URL, params=params, timeout=10)

            if resp.status_code == 429:
                # Google returned quota-exceeded — stop immediately and fall back
                logger.warning("Google CSE: 429 quota exceeded — stopping, falling back to Google News RSS")
                diagnostics["search_errors"].append({"region": region, "query": query_text, "reason": "cse_quota_429"})
                _cse_save_usage(_CSE_DAILY_LIMIT)  # Mark as exhausted for the rest of the day
                quota_exhausted = True
                break

            if resp.status_code != 200:
                diagnostics["search_errors"].append({"region": region, "query": query_text, "reason": f"http_{resp.status_code}"})
                errors += 1
                continue

            # Successful query — count it
            used_count += 1
            _cse_save_usage(used_count)

            data = resp.json()
            items = data.get("items") or []
            diagnostics.setdefault("search_queries", []).append({"region": region, "query": query_text, "source": "cse"})

            for item in items:
                title = clean_text(item.get("title", ""))
                link = item.get("link", "")
                snippet = clean_text(item.get("snippet", ""))
                if not title or not link:
                    continue
                # Try to get publish date from pagemap metatags
                published = ""
                pagemap = item.get("pagemap") or {}
                for metatag in (pagemap.get("metatags") or []):
                    pub = metatag.get("article:published_time") or metatag.get("date") or ""
                    if pub:
                        published = pub
                        break

                domain = domain_of(link)
                matched_source = next(
                    (src for src_domain, src in source_by_domain.items() if src_domain in domain),
                    None,
                )
                candidates.append(
                    Candidate(
                        title=title,
                        url=normalize_url("", link),
                        source=matched_source.name if matched_source else (domain or "Google CSE"),
                        source_url=matched_source.url if matched_source else "",
                        country=matched_source.country if matched_source else (
                            "Argentina" if region == "Local" else region
                        ),
                        region=matched_source.region if matched_source else region,
                        language=matched_source.language if matched_source else (
                            "es" if region in ("Local", "Regional") else "en"
                        ),
                        published=published,
                        summary=snippet,
                        discovery=f"google_cse:{query_text[:60]}",
                        trade_source=matched_source.trade if matched_source else False,
                        require_section=matched_source.require_section if matched_source else False,
                    )
                )

        except Exception as exc:
            diagnostics["search_errors"].append({"region": region, "query": query_text, "reason": str(exc)[:80]})
            errors += 1
            if errors >= 5:
                logger.warning("Google CSE: %d consecutive errors — aborting", errors)
                break

    logger.info(
        "Google CSE: %d candidates from %d queries (quota usado hoy: %d/%d)",
        len(candidates), len(queries), used_count, _CSE_DAILY_LIMIT,
    )
    return candidates, quota_exhausted


def discover_candidates(companies, keywords, sources, enable_search=True, max_search_queries=55):
    diagnostics = {
        "source_counts": {},
        "source_errors": [],
        "section_counts": {},
        "section_errors": [],
        "discovered_feeds": {},
        "search_queries": [],
        "search_errors": [],
    }
    logger.info("Discovery: starting RSS fetch from %d sources", len(sources))
    candidates = discover_from_rss(sources, diagnostics)
    logger.info("Discovery: %d candidates from RSS", len(candidates))

    before_sections = len(candidates)
    candidates.extend(discover_from_sections(sources, diagnostics))
    logger.info("Discovery: %d candidates from sections", len(candidates) - before_sections)

    if enable_search:
        before_search = len(candidates)
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        cse_id = os.environ.get("GOOGLE_CSE_ID", "").strip()
        use_rss_fallback = True

        if api_key and cse_id:
            cse_results, quota_exhausted = discover_from_google_cse(
                companies, keywords, sources, diagnostics, max_queries=max_search_queries
            )
            candidates.extend(cse_results)
            logger.info("Discovery: %d candidates from Google CSE", len(candidates) - before_search)
            # Fall back to Google News RSS if quota exhausted OR CSE returned nothing (errors, blocks, etc.)
            use_rss_fallback = quota_exhausted or len(cse_results) == 0
            if quota_exhausted:
                logger.info("Discovery: CSE quota exhausted — falling back to Google News RSS for this run")
            elif len(cse_results) == 0:
                logger.info("Discovery: CSE returned 0 candidates — falling back to Google News RSS")

        if use_rss_fallback:
            before_rss = len(candidates)
            candidates.extend(discover_from_google_news(companies, keywords, sources, diagnostics, max_queries=max_search_queries))
            logger.info("Discovery: %d candidates from Google News RSS", len(candidates) - before_rss)

    if diagnostics["source_errors"]:
        logger.warning("Discovery: %d RSS source errors", len(diagnostics["source_errors"]))
    if diagnostics["search_errors"]:
        logger.warning("Discovery: %d search errors (CSE + Google News)", len(diagnostics["search_errors"]))

    diagnostics["candidates_found"] = len(candidates)
    logger.info("Discovery: %d total candidates found", len(candidates))
    return candidates, diagnostics
