from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
from zoneinfo import ZoneInfo


TOPIC_LABELS = {
    "product_innovation":       "Product Innovation",
    "marketing_innovation":     "Marketing & Innovation",
    "financial_results":        "Financial Results",
    "ma_and_strategy":          "M&A / Strategy",
    "distribution_execution":   "Distribution & Execution",
    "regulation_tax_policy":    "Regulation & Policy",
    "packaging_sustainability":  "Packaging & Sustainability",
    "consumer_market_trends":   "Consumer & Market Trends",
    "supply_chain_commodities": "Supply Chain & Commodities",
    "risk_crisis_reputation":   "Risk & Reputation",
    "non_alcoholic_beverages":  "Non-Alcoholic Beverages",
    "alternative_ingredients":  "Alternative Ingredients",
    "company_news":             "Company News",
}

AUDIENCE_TOPICS = {
    "Marketing":  ["marketing_innovation", "product_innovation", "consumer_market_trends"],
    "Finanzas":   ["financial_results", "ma_and_strategy"],
    "I+D":        ["product_innovation", "alternative_ingredients", "packaging_sustainability"],
    "Logística":  ["distribution_execution", "supply_chain_commodities"],
    "Planning":   ["consumer_market_trends", "ma_and_strategy", "regulation_tax_policy", "risk_crisis_reputation"],
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
    primary_topic = segments[0] if segments else "company_news"
    topic_color = TOPIC_COLORS.get(primary_topic, "#374151")
    body_html = escape(article["body"]).replace("\n", "<br>")
    summary = article["summary"] or "No source summary available."
    pub_date = _format_date(article["published"])

    translate_url = f"https://translate.google.com/translate?sl=auto&tl=es&u={escape(article['url'], quote=True)}"
    pub_iso = escape(article["published"] or "", quote=True)
    return f"""
        <article class="news-card"
            data-search="{escape(search, quote=True)}"
            data-segments="{escape('|'.join(segments), quote=True)}"
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
            <p class="summary">{escape(summary)}</p>
            <div class="card-actions">
                <a class="original-btn" href="{escape(article['url'], quote=True)}" target="_blank" rel="noopener noreferrer">Read original</a>
                <a class="translate-btn" href="{translate_url}" target="_blank" rel="noopener noreferrer">Translate to Spanish</a>
            </div>
            <details>
                <summary>Full article text</summary>
                <div class="body-text">{body_html}</div>
            </details>
        </article>
    """


def _sections_html(article_dicts):
    if not article_dicts:
        return '<p class="empty-state">No articles passed the current filters or extraction rules.</p>'

    grouped = {}
    for article in article_dicts:
        primary = article["segments"][0] if article["segments"] else "company_news"
        grouped.setdefault(primary, []).append(article)

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
                    <span>{len(items)} ARTICLES</span>
                </div>
                <div class="grid">
                    {cards}
                </div>
            </section>
            """
        )
    return "\n".join(sections)


def generate_web(articles, diagnostics=None, output_path="index.html"):
    article_dicts = [_article_dict(article) for article in articles]
    now = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))

    segments = _sort_topics(_uniq(segment for article in article_dicts for segment in article["segments"]))
    companies = _uniq(company for article in article_dicts for company in article["companies"])
    regions = _sort_regions(_uniq(article["region"] for article in article_dicts))
    countries = _uniq(article["country"] for article in article_dicts)
    sources = _uniq(article["source"] for article in article_dicts)
    languages = _uniq(article["language"] for article in article_dicts)

    sections_html = _sections_html(article_dicts)

    # Pre-compute AUDIENCE_TOPICS as JS object literal (can't use nested f-strings with quotes)
    _aud_parts = []
    for _k, _v in AUDIENCE_TOPICS.items():
        _topics = ", ".join('"' + t + '"' for t in _v)
        _aud_parts.append('"' + _k + '": [' + _topics + ']')
    audience_topics_js = "{" + ", ".join(_aud_parts) + "}"

    diagnostics = diagnostics or {}
    discovery_count = diagnostics.get("discovery", {}).get("candidates_found", 0)
    accepted_count = diagnostics.get("filtering", {}).get("accepted", 0)
    extracted_count = diagnostics.get("extraction", {}).get("extracted", len(article_dicts))
    selected_by_region = diagnostics.get("selection", {}).get("selected_by_region", {})
    region_stats = " · ".join(f"{region}: {selected_by_region.get(region, 0)}" for region in ("Local", "Regional", "Mundial"))

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
        .original-btn:hover, .translate-btn:hover {{
            filter: brightness(0.97);
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
        <p class="eyebrow">Beverage Sector</p>
        <h1>Industry News Update</h1>
        <p class="intro">A curated digest of the latest news from the global beverage industry — covering product innovation, corporate moves, consumer trends, and regulatory changes — to stay ahead of what's happening, build better products, and make smarter business decisions.</p>
        <p class="timestamp">Updated {escape(now.strftime("%b %d, %Y at %H:%M"))} (Buenos Aires time)</p>
        <div class="toolbar">
            <input type="search" id="search" placeholder="Search by title, company, source or country…" aria-label="Search articles">
            <button class="clear-btn" type="button" id="clear">Clear</button>
        </div>
        <div class="filter-panel">
            <p class="filter-panel-title">Filter articles</p>
            <div class="filter-row">
                <span class="filter-row-label">Coverage</span>
                <div class="filters">{_filter_buttons("region", regions, REGION_LABELS)}</div>
            </div>
            <div class="filter-row">
                <span class="filter-row-label">Topic</span>
                <div class="filters" id="topic-filters">{_filter_buttons("segments", segments, TOPIC_LABELS)}</div>
            </div>
        </div>
    </header>
    <main>
        <p class="result-count" id="count"></p>
        <div id="sections">
            {sections_html}
        </div>
        <p class="empty-state" id="empty" hidden>No articles match the current filters.</p>
    </main>
    <script>
        const AUDIENCE_TOPICS = {audience_topics_js};

        const search = document.querySelector("#search");
        const clear = document.querySelector("#clear");
        const count = document.querySelector("#count");
        const empty = document.querySelector("#empty");
        const cards = Array.from(document.querySelectorAll(".news-card"));
        const sections = Array.from(document.querySelectorAll(".topic-section"));
        const activeFilters = new Map();
        let activeAudience = null;

        function normalize(text) {{
            return (text || "").toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "");
        }}

        function relativeTime(isoStr) {{
            if (!isoStr) return "";
            try {{
                const dt = new Date(isoStr);
                const diffH = Math.floor((Date.now() - dt) / 3600000);
                if (diffH < 1) return "< 1h ago";
                if (diffH < 24) return diffH + "h ago";
                const diffD = Math.floor(diffH / 24);
                return diffD === 1 ? "yesterday" : diffD + "d ago";
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
                let matchesAudience = true;
                if (activeAudience) {{
                    const allowed = AUDIENCE_TOPICS[activeAudience] || [];
                    const segs = (card.dataset.segments || "").split("|");
                    matchesAudience = allowed.some((t) => segs.includes(t));
                }}
                const show = matchesSearch && matchesFilters && matchesAudience;
                card.hidden = !show;
                if (show) visible += 1;
            }});
            sections.forEach((section) => {{
                const sectionCards = Array.from(section.querySelectorAll(".news-card"));
                section.hidden = sectionCards.every((card) => card.hidden);
            }});
            count.textContent = `${{visible}} ARTICLES`;
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

        document.querySelectorAll(".filter[data-audience]").forEach((button) => {{
            button.addEventListener("click", () => {{
                document.querySelectorAll(".filter[data-audience]").forEach((b) => b.classList.remove("active"));
                button.classList.add("active");
                const aud = button.dataset.audience;
                activeAudience = aud === "ALL" ? null : aud;
                applyFilters();
            }});
        }});

        search.addEventListener("input", applyFilters);
        clear.addEventListener("click", () => {{
            search.value = "";
            activeFilters.clear();
            activeAudience = null;
            document.querySelectorAll(".filter[data-filter]").forEach((button) => button.classList.toggle("active", button.dataset.value === "ALL"));
            document.querySelectorAll(".filter[data-audience]").forEach((button) => button.classList.toggle("active", button.dataset.audience === "ALL"));
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

    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(html)
