import email.utils
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from .http import fetch_text
from .models import Candidate
from .text import clean_text
from .urls import domain_of, normalize_url


GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl={hl}&gl={gl}&ceid={ceid}"
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


def _parse_date(value):
    if not value:
        return ""
    try:
        return email.utils.parsedate_to_datetime(value).isoformat()
    except Exception:
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
    for region, region_key, queries in query_groups:
        for query in queries:
            search_queries.append({"region": region, "query": query})
            rss_url = _google_news_url(query, region_key)
            xml_text, status = fetch_text(rss_url)
            if not xml_text:
                diagnostics["search_errors"].append({"region": region, "query": query, "reason": status})
                continue
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError:
                diagnostics["search_errors"].append({"region": region, "query": query, "reason": "rss_parse_error"})
                continue

            for item in root.findall(".//item"):
                title = _xml_text(item, "title")
                link = normalize_url("", _xml_text(item, "link"))
                description = clean_text(BeautifulSoup(_xml_text(item, "description"), "html.parser").get_text(" "))
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
                    )
                )

    diagnostics["search_queries"] = search_queries
    return candidates


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
    candidates = discover_from_rss(sources, diagnostics)
    candidates.extend(discover_from_sections(sources, diagnostics))
    if enable_search:
        candidates.extend(discover_from_google_news(companies, keywords, sources, diagnostics, max_queries=max_search_queries))
    diagnostics["candidates_found"] = len(candidates)
    return candidates, diagnostics
