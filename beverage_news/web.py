from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from pathlib import Path
import shutil
import time
from zoneinfo import ZoneInfo


TOPIC_LABELS = {
    "product_innovation":       "Innovación de Producto",
    "marketing_innovation":     "Marketing e Innovación",
    "financial_results":        "Resultados Financieros",
    "ma_and_strategy":          "M&A / Estrategia",
    "distribution_execution":   "Distribución y Ejecución",
    "regulation_tax_policy":    "Regulación y Política",
    "packaging_sustainability":  "Packaging y Sustentabilidad",
    "consumer_market_trends":   "Tendencias del Consumidor",
    "supply_chain_commodities": "Cadena de Suministro",
    "risk_crisis_reputation":   "Riesgo y Reputación",
    "non_alcoholic_beverages":  "Bebidas Sin Alcohol",
    "alternative_ingredients":  "Ingredientes Alternativos",
    "company_news":             "Noticias Corporativas",
}


REGION_LABELS = {
    "Local":    "Local",
    "Regional": "Regional",
    "Mundial":  "Global",
}

TOPIC_COLORS = {
    "product_innovation":       "#0e5f57",
    "marketing_innovation":     "#7c3aed",
    "financial_results":        "#b45309",
    "ma_and_strategy":          "#1d4ed8",
    "distribution_execution":   "#c2570a",
    "regulation_tax_policy":    "#dc2626",
    "packaging_sustainability":  "#15803d",
    "consumer_market_trends":   "#0891b2",
    "supply_chain_commodities": "#92400e",
    "risk_crisis_reputation":   "#9f1239",
    "non_alcoholic_beverages":  "#0369a1",
    "alternative_ingredients":  "#4d7c0f",
    "company_news":             "#374151",
}

REGION_COLORS = {
    "Local":    "#0e5f57",
    "Regional": "#b45309",
    "Mundial":  "#4c5fa3",
}

MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _format_date(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc)
        return f"{dt.day} {MONTHS_EN[dt.month - 1]} {dt.year} · {dt.strftime('%H:%M')}"
    except Exception:
        return iso_str

TOPIC_ORDER = {topic: index for index, topic in enumerate(TOPIC_LABELS)}
REGION_ORDER = {"Local": 0, "Regional": 1, "Mundial": 2}


def _uniq(values):
    return sorted(dict.fromkeys(value for value in values if value))


def _label_topic(value):
    return TOPIC_LABELS.get(value, value.replace("_", " ").title())


def _sort_topics(values):
    return sorted(values, key=lambda value: (TOPIC_ORDER.get(value, len(TOPIC_ORDER)), value))


def _sort_regions(values):
    return sorted(values, key=lambda value: (REGION_ORDER.get(value, len(REGION_ORDER)), value))


def _article_dict(article):
    data = asdict(article) if hasattr(article, "__dataclass_fields__") else dict(article)
    data["segments"] = _sort_topics(_uniq(data.get("segments") or []))
    data["companies"] = _uniq(data.get("companies") or [])
    data["keyword_categories"] = _uniq(data.get("keyword_categories") or [])
    data["region"] = data.get("region") or "Mundial"
    data["primary_topic"] = data["segments"][0] if data["segments"] else "company_news"
    return data


def _chips(values, css_class="chip"):
    return "".join(f'<span class="{css_class}">{escape(str(value))}</span>' for value in values)


def _filter_buttons(name, values, labels=None):
    buttons = [f'<button class="filter active" type="button" data-filter="{name}" data-value="ALL">Todo</button>']
    for value in values:
        label = labels.get(value, value) if labels else value
        buttons.append(
            f'<button class="filter" type="button" data-filter="{name}" data-value="{escape(value, quote=True)}">{escape(label)}</button>'
        )
    return "".join(buttons)



def _article_html(article):
    segments = article["segments"]
    companies = article["companies"]
    keywords = article["keyword_categories"]
    primary_topic = article["primary_topic"]
    search = " ".join(
        [
            article["title"],
            article["summary"],
            article["body"],
            article["source"],
            article["country"],
            article["region"],
            article["language"],
            " ".join(companies),
            " ".join(segments),
            " ".join(keywords),
        ]
    )

    region = article["region"] or "Mundial"
    region_color = REGION_COLORS.get(region, "#4c5fa3")
    topic_color = TOPIC_COLORS.get(primary_topic, "#374151")
    body_html = escape(article["body"]).replace("\n", "<br>")
    llm_summary = (article.get("llm_summary") or "").strip()
    rss_summary = (article.get("summary") or "").strip()
    displayed_summary = llm_summary or rss_summary or "Sin resumen disponible."
    pub_date = _format_date(article["published"])

    translate_url = f"https://translate.google.com/translate?sl=auto&tl=es&u={escape(article['url'], quote=True)}"
    pub_iso = escape(article["published"] or "", quote=True)

    # Build extra source links for merged duplicates (skip unresolved google.com URLs)
    merged_sources = article.get("merged_sources") or []
    extra_links = ""
    for entry in merged_sources:
        if "|||" in entry:
            src_name, src_url = entry.split("|||", 1)
        else:
            src_name, src_url = "Fuente alternativa", entry
        if "news.google.com" in src_url or "google.com/url" in src_url:
            continue  # Skip unresolved Google News redirects — not a real source link
        extra_links += (
            f'<a class="alt-source-btn" href="{escape(src_url, quote=True)}" '
            f'target="_blank" rel="noopener noreferrer">'
            f'También en: {escape(src_name)}</a>'
        )

    return f"""
        <article class="news-card"
            data-search="{escape(search, quote=True)}"
            data-segments="{escape('|'.join(segments), quote=True)}"
            data-topic="{escape(primary_topic, quote=True)}"
            data-companies="{escape('|'.join(companies), quote=True)}"
            data-region="{escape(region, quote=True)}"
            data-country="{escape(article['country'], quote=True)}"
            data-source="{escape(article['source'], quote=True)}"
            data-language="{escape(article['language'], quote=True)}"
            style="border-left-color:{topic_color}">
            <div class="card-topline">
                <span class="region-badge" style="background:{region_color}">{escape(REGION_LABELS.get(region, region).upper())}</span>
                <span class="topline-source">{escape(article['source'])}</span>
                <span class="topline-date" data-iso="{pub_iso}" title="{escape(pub_date)}">{escape(pub_date)}</span>
            </div>
            <h2>{escape(article["title"])}</h2>
            <p class="summary">{escape(displayed_summary)}</p>
            <div class="card-actions">
                <a class="original-btn" href="{escape(article['url'], quote=True)}" target="_blank" rel="noopener noreferrer">Leer nota original</a>
                <a class="translate-btn" href="{translate_url}" target="_blank" rel="noopener noreferrer">Ver en español</a>
                {extra_links}
            </div>
            <details>
                <summary>Texto completo del artículo</summary>
                <div class="body-text">{body_html}</div>
            </details>
        </article>
    """


_HIGHLIGHT_TOPIC_WEIGHTS = {
    "financial_results": 30,
    "ma_and_strategy": 26,
    "product_innovation": 24,
    "marketing_innovation": 22,
    "distribution_execution": 20,
    "regulation_tax_policy": 19,
    "risk_crisis_reputation": 18,
    "packaging_sustainability": 16,
    "consumer_market_trends": 14,
    "supply_chain_commodities": 12,
    "non_alcoholic_beverages": 12,
    "alternative_ingredients": 10,
    "company_news": 6,
}
_HIGHLIGHT_PRIORITY_COMPANIES = {
    "Red Bull", "Monster Beverage", "Celsius Holdings", "Olipop", "Poppi",
    "Fevertree Drinks", "AB InBev", "Diageo", "Campari Group",
    "Constellation Brands", "The Coca-Cola Company", "PepsiCo",
    "Coca-Cola FEMSA", "Arca Continental",
}


def _highlight_score(article):
    score = 0
    topics = article.get("segments") or []
    if topics:
        score += max(_HIGHLIGHT_TOPIC_WEIGHTS.get(t, 0) for t in topics)
    companies = set(article.get("companies") or [])
    if companies:
        score += 18
        score += sum(8 for c in companies if c in _HIGHLIGHT_PRIORITY_COMPANIES)
    # Recencia
    try:
        dt = datetime.fromisoformat((article.get("published") or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
        if hours <= 12: score += 10
        elif hours <= 24: score += 6
        elif hours <= 36: score += 3
    except Exception:
        pass
    return score


def _pick_highlights(article_dicts, n=5):
    if not article_dicts:
        return []
    ranked = sorted(article_dicts, key=_highlight_score, reverse=True)
    return ranked[:n]


def _highlights_html(article_dicts):
    highlights = _pick_highlights(article_dicts, n=5)
    if not highlights:
        return ""
    cards = "\n".join(_article_html(article) for article in highlights)
    return f"""
        <section class="highlights-section">
            <div class="section-heading highlights-heading">
                <h2>Destacados de hoy</h2>
                <span>TOP {len(highlights)}</span>
            </div>
            <div class="grid">
                {cards}
            </div>
        </section>
    """


def _sections_html(article_dicts):
    if not article_dicts:
        return '<p class="empty-state">No articles passed the current filters or extraction rules.</p>'

    grouped = {}
    for article in article_dicts:
        grouped.setdefault(article["primary_topic"], []).append(article)

    sections = []
    for topic in _sort_topics(grouped):
        items = grouped[topic]
        topic_color = TOPIC_COLORS.get(topic, "#374151")
        cards = "\n".join(_article_html(article) for article in items)
        sections.append(
            f"""
            <section class="topic-section" data-topic="{escape(topic, quote=True)}">
                <div class="section-heading">
                    <h2 style="border-left-color:{topic_color}">{escape(_label_topic(topic))}</h2>
                    <span>{len(items)} NOTICIAS</span>
                </div>
                <div class="grid">
                    {cards}
                </div>
            </section>
            """
        )
    return "\n".join(sections)


def generate_web(articles, diagnostics=None, output_path="index.html", qa=None):
    article_dicts = [_article_dict(article) for article in articles]
    now = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    primary_topics = _sort_topics(_uniq(article["primary_topic"] for article in article_dicts))
    companies = _uniq(company for article in article_dicts for company in article["companies"])
    regions = _sort_regions(_uniq(article["region"] for article in article_dicts))
    countries = _uniq(article["country"] for article in article_dicts)
    sources = _uniq(article["source"] for article in article_dicts)
    languages = _uniq(article["language"] for article in article_dicts)

    sections_html = _sections_html(article_dicts)
    highlights_html = _highlights_html(article_dicts)

    # Indicador de salud
    disc = diagnostics.get("discovery", {}) if diagnostics else {}
    source_errors = len(disc.get("source_errors", []) or [])
    section_errors = len(disc.get("section_errors", []) or [])
    total_errors = source_errors + section_errors
    if total_errors == 0:
        health_label, health_color = "OK", "#0e5f57"
    elif total_errors <= 3:
        health_label, health_color = "WARN", "#b45309"
    else:
        health_label, health_color = "DEGRADED", "#9f1239"

    diagnostics = diagnostics or {}
    discovery_count = diagnostics.get("discovery", {}).get("candidates_found", 0)
    accepted_count = diagnostics.get("filtering", {}).get("accepted", 0)
    extracted_count = diagnostics.get("extraction", {}).get("extracted", len(article_dicts))
    selected_by_region = diagnostics.get("selection", {}).get("selected_by_region", {})
    region_stats = " · ".join(f"{region}: {selected_by_region.get(region, 0)}" for region in ("Local", "Regional", "Mundial"))

    # QA result is internal only — not rendered in the dashboard

    html = f"""<!doctype html>
<html lang="es">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Industry News Update — Beverage Sector</title>
    <style>
        * {{ box-sizing: border-box; }}
        :root {{
            --bg: #f5f6f4;
            --surface: #ffffff;
            --surface-soft: #eef1ef;
            --text: #1d2328;
            --muted: #5d6770;
            --border: #d8ddd9;
            --accent: #0e5f57;
            --accent-soft: #dcebe7;
            --amber: #8b5e12;
        }}
        body {{
            margin: 0;
            color: var(--text);
            background: var(--bg);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
            letter-spacing: 0;
        }}
        header, main {{
            max-width: 1280px;
            margin: 0 auto;
            padding: 22px 18px;
        }}
        header {{
            padding-bottom: 10px;
        }}
        .eyebrow {{
            margin: 0 0 7px;
            color: var(--accent);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        h1 {{
            margin: 0;
            font-size: 34px;
            line-height: 1.08;
        }}
        .intro {{
            max-width: 860px;
            margin: 10px 0 0;
            color: var(--muted);
            font-size: 15px;
            line-height: 1.5;
        }}
        .timestamp {{
            margin: 6px 0 0;
            color: var(--muted);
            font-size: 12px;
        }}
        .stats {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 14px;
        }}
        .stat {{
            min-height: 34px;
            padding: 8px 10px;
            border: 1px solid var(--border);
            border-radius: 7px;
            background: var(--surface);
            color: var(--muted);
            font-size: 12px;
        }}
        .stat strong {{
            color: var(--text);
            font-size: 15px;
        }}
        .toolbar {{
            display: grid;
            grid-template-columns: minmax(240px, 1fr) auto;
            gap: 8px;
            margin-top: 14px;
        }}
        input[type="search"] {{
            width: 100%;
            min-height: 38px;
            border: 1px solid var(--border);
            border-radius: 7px;
            padding: 0 12px;
            background: var(--surface);
            color: var(--text);
            font-size: 14px;
        }}
        .clear-btn {{
            border: 1px solid var(--border);
            border-radius: 7px;
            padding: 0 12px;
            background: var(--surface);
            cursor: pointer;
            font-weight: 700;
        }}
        .filter-panel {{
            margin-top: 16px;
            padding: 14px 16px;
            border: 1px solid var(--border);
            border-radius: 10px;
            background: var(--surface);
        }}
        .filter-panel-title {{
            margin: 0 0 12px;
            font-size: 14px;
            font-weight: 700;
            color: var(--text);
        }}
        .filter-row {{
            display: flex;
            align-items: flex-start;
            gap: 14px;
            margin-top: 8px;
        }}
        .filter-row:first-of-type {{
            margin-top: 0;
        }}
        .filter-row-label {{
            min-width: 80px;
            padding-top: 5px;
            color: var(--muted);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            flex-shrink: 0;
        }}
        .filters {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }}
        .filter {{
            min-height: 29px;
            border: 1px solid var(--border);
            border-radius: 50px;
            padding: 0 14px;
            background: var(--bg);
            color: var(--muted);
            cursor: pointer;
            font-size: 12px;
            font-weight: 700;
        }}
        .filter.active {{
            border-color: var(--accent);
            background: var(--accent);
            color: #fff;
        }}
        .result-count {{
            margin: 20px 0 10px;
            color: var(--muted);
            font-size: 12px;
            font-weight: 700;
            text-transform: uppercase;
        }}
        .highlights-section {{
            margin-top: 24px;
            padding: 14px 16px 18px;
            border: 1px solid var(--accent);
            border-radius: 10px;
            background: linear-gradient(180deg, #f0f7f5 0%, #ffffff 70%);
        }}
        .highlights-heading h2 {{
            border-left-color: var(--accent) !important;
            color: var(--accent);
        }}
        .health-pill {{
            display: inline-block;
            padding: 1px 7px;
            border-radius: 4px;
            color: #fff;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
        }}
        .health-detail {{
            color: var(--muted);
            font-size: 12px;
        }}
        .topic-section {{
            margin-top: 32px;
        }}
        .section-heading {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 8px;
            padding-bottom: 6px;
            border-bottom: 1px solid var(--border);
        }}
        .section-heading h2 {{
            margin: 0;
            font-size: 22px;
            line-height: 1.15;
            border-left: 4px solid var(--accent);
            padding-left: 10px;
        }}
        .section-heading span {{
            color: var(--muted);
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            white-space: nowrap;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(2, minmax(0, 1fr));
            gap: 10px;
        }}
        .news-card {{
            border: 1px solid var(--border);
            border-left: 4px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            background: var(--surface);
        }}
        .card-topline {{
            display: flex;
            align-items: center;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 8px;
        }}
        .region-badge {{
            display: inline-block;
            padding: 2px 7px;
            border-radius: 4px;
            font-size: 10px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: #fff;
        }}
        .topline-source {{
            color: var(--text);
            font-size: 12px;
            font-weight: 600;
        }}
        .topline-date {{
            color: var(--muted);
            font-size: 12px;
        }}
        .llm-badge {{
            margin-left: auto;
            font-size: 10px;
            font-weight: 700;
            letter-spacing: 0.03em;
            color: #7c3aed;
            background: #f3eeff;
            border: 1px solid #c4b5fd;
            border-radius: 4px;
            padding: 2px 6px;
        }}
        .news-card h2 {{
            margin: 0 0 8px;
            font-size: 19px;
            font-weight: 700;
            line-height: 1.22;
        }}
        .summary {{
            margin: 0 0 12px;
            color: var(--muted);
            font-size: 14px;
            line-height: 1.45;
        }}
        .card-actions {{
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-bottom: 10px;
        }}
        .original-btn, .translate-btn {{
            display: inline-flex;
            align-items: center;
            min-height: 31px;
            border-radius: 6px;
            padding: 0 10px;
            font-size: 12px;
            font-weight: 700;
            text-decoration: none;
        }}
        .original-btn {{
            border: 1px solid var(--accent);
            color: var(--accent);
            background: var(--accent-soft);
        }}
        .translate-btn {{
            border: 1px solid var(--border);
            color: var(--muted);
            background: var(--surface-soft);
        }}
        .original-btn:hover, .translate-btn:hover, .alt-source-btn:hover {{
            filter: brightness(0.97);
        }}
        .alt-source-btn {{
            display: inline-flex;
            align-items: center;
            min-height: 31px;
            border-radius: 6px;
            padding: 0 10px;
            font-size: 12px;
            font-weight: 700;
            text-decoration: none;
            border: 1px solid #c4b5fd;
            color: #6d28d9;
            background: #f5f3ff;
        }}
        details {{
            border-top: 1px solid var(--border);
            margin-top: 4px;
            padding-top: 12px;
        }}
        details summary {{
            cursor: pointer;
            color: var(--accent);
            font-size: 13px;
            font-weight: 700;
            list-style: none;
        }}
        details summary::-webkit-details-marker {{ display: none; }}
        details summary::marker {{ display: none; }}
        details summary::before {{ content: "▶  "; font-size: 10px; }}
        details[open] summary::before {{ content: "▼  "; }}
        .body-text {{
            margin-top: 10px;
            color: var(--text);
            font-size: 14px;
            line-height: 1.55;
            white-space: normal;
        }}
        .empty-state {{
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 18px;
            background: var(--surface);
            color: var(--muted);
            text-align: center;
        }}
        .qa-block {{
            margin-top: 16px;
            padding: 14px 16px;
            border: 1px solid var(--accent);
            border-radius: 10px;
            background: linear-gradient(135deg, #f0f7f5 0%, #ffffff 100%);
        }}
        .qa-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 8px;
        }}
        .qa-label {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            color: var(--accent);
            letter-spacing: 0.05em;
        }}
        .qa-score {{
            font-size: 11px;
            font-weight: 700;
            color: #fff;
            border-radius: 4px;
            padding: 2px 7px;
        }}
        .qa-briefing {{
            margin: 0;
            color: var(--text);
            font-size: 14px;
            line-height: 1.5;
        }}
        .qa-warnings {{
            margin: 8px 0 0;
            padding-left: 18px;
            color: #b45309;
            font-size: 13px;
        }}
        @media (max-width: 860px) {{
            h1 {{ font-size: 28px; }}
            .toolbar {{ grid-template-columns: 1fr; }}
            .grid {{ grid-template-columns: 1fr; }}
            .filter-row {{ flex-direction: column; gap: 6px; }}
            .filter-row-label {{ min-width: unset; padding-top: 0; }}
        }}
    </style>
</head>
<body>
    <header>
        <p class="eyebrow">Sector de Bebidas</p>
        <h1>Actualización de Noticias del Sector</h1>
        <p class="intro">Resumen curado de las últimas noticias de la industria global de bebidas — innovación de producto, movimientos corporativos, tendencias del consumidor y cambios regulatorios — para estar un paso adelante, desarrollar mejores productos y tomar decisiones más inteligentes.</p>
        <p class="timestamp">Actualizado el {escape(now.strftime("%d/%m/%Y a las %H:%M"))} (hora Buenos Aires)</p>
        <div class="toolbar">
            <input type="search" id="search" placeholder="Buscar por título, empresa, fuente o país…" aria-label="Buscar noticias">
            <button class="clear-btn" type="button" id="clear">Limpiar</button>
        </div>
        <div class="filter-panel">
            <p class="filter-panel-title">Filtrar noticias</p>
            <div class="filter-row">
                <span class="filter-row-label">Cobertura</span>
                <div class="filters">{_filter_buttons("region", regions, REGION_LABELS)}</div>
            </div>
            <div class="filter-row">
                <span class="filter-row-label">Tópico</span>
                <div class="filters" id="topic-filters">{_filter_buttons("topic", primary_topics, TOPIC_LABELS)}</div>
            </div>
        </div>
    </header>
    <main>
        <p class="result-count" id="count"></p>
        <div id="highlights">
            {highlights_html}
        </div>
        <div id="sections">
            {sections_html}
        </div>
        <p class="empty-state" id="empty" hidden>Ninguna noticia coincide con los filtros seleccionados.</p>
    </main>
    <script>
        const search = document.querySelector("#search");
        const clear = document.querySelector("#clear");
        const count = document.querySelector("#count");
        const empty = document.querySelector("#empty");
        const cards = Array.from(document.querySelectorAll(".news-card"));
        const topicCards = Array.from(document.querySelectorAll(".topic-section .news-card"));
        const sections = Array.from(document.querySelectorAll(".topic-section"));
        const highlightsSection = document.querySelector(".highlights-section");
        const activeFilters = new Map();

        function normalize(text) {{
            return (text || "").toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "");
        }}

        function relativeTime(isoStr) {{
            if (!isoStr) return "";
            try {{
                const dt = new Date(isoStr);
                const diffH = Math.floor((Date.now() - dt) / 3600000);
                if (diffH < 1) return "hace < 1h";
                if (diffH < 24) return "hace " + diffH + "h";
                const diffD = Math.floor(diffH / 24);
                return diffD === 1 ? "ayer" : "hace " + diffD + "d";
            }} catch(e) {{ return ""; }}
        }}

        function cardHas(card, filter, value) {{
            if (value === "ALL") return true;
            const raw = card.dataset[filter] || "";
            if (filter === "segments" || filter === "companies") {{
                return raw.split("|").includes(value);
            }}
            return raw === value;
        }}

        function applyFilters() {{
            const terms = normalize(search.value).split(/\\s+/).filter((term) => term.length >= 2);
            let visible = 0;
            cards.forEach((card) => {{
                const text = normalize(card.dataset.search || "");
                const matchesSearch = terms.every((term) => text.includes(term));
                const matchesFilters = Array.from(activeFilters.entries()).every(([filter, value]) => cardHas(card, filter, value));
                const show = matchesSearch && matchesFilters;
                card.hidden = !show;
                if (show && topicCards.includes(card)) visible += 1;
            }});
            sections.forEach((section) => {{
                const sectionCards = Array.from(section.querySelectorAll(".news-card"));
                section.hidden = sectionCards.every((card) => card.hidden);
            }});
            if (highlightsSection) {{
                const hlCards = Array.from(highlightsSection.querySelectorAll(".news-card"));
                highlightsSection.hidden = hlCards.every((card) => card.hidden);
            }}
            count.textContent = `${{visible}} NOTICIAS`;
            empty.hidden = visible !== 0;
        }}

        document.querySelectorAll(".filter[data-filter]").forEach((button) => {{
            button.addEventListener("click", () => {{
                const filter = button.dataset.filter;
                const value = button.dataset.value;
                document.querySelectorAll(`.filter[data-filter="${{filter}}"]`).forEach((item) => item.classList.remove("active"));
                button.classList.add("active");
                if (value === "ALL") activeFilters.delete(filter);
                else activeFilters.set(filter, value);
                applyFilters();
            }});
        }});

        search.addEventListener("input", applyFilters);
        clear.addEventListener("click", () => {{
            search.value = "";
            activeFilters.clear();
            document.querySelectorAll(".filter[data-filter]").forEach((button) => button.classList.toggle("active", button.dataset.value === "ALL"));
            applyFilters();
        }});

        document.querySelectorAll(".topline-date[data-iso]").forEach((el) => {{
            const rel = relativeTime(el.dataset.iso);
            if (rel) el.textContent = rel;
        }});

        applyFilters();
    </script>
</body>
</html>
"""

    output = Path(output_path).resolve()
    temp_output = output.with_suffix(output.suffix + ".tmp")
    with open(temp_output, "w", encoding="utf-8") as handle:
        handle.write(html)
    for attempt in range(5):
        try:
            temp_output.replace(output)
            break
        except PermissionError:
            if attempt == 4:
                shutil.copyfile(temp_output, output)
                try:
                    temp_output.unlink()
                except PermissionError:
                    pass
                break
            time.sleep(0.2)
