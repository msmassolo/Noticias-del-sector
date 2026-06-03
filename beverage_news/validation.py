import logging
import re
from datetime import datetime, timezone, timedelta

from .text import term_in_text

MAX_AGE_HOURS_POST_EXTRACTION = 36

logger = logging.getLogger(__name__)

MIN_TITLE_LEN = 15
MIN_SUMMARY_LEN = 30
MAX_TITLE_LEN = 250
TRADE_SOURCES = {
    "Just Drinks",
    "FoodBev Media",
    "The Drinks Business",
    "The Spirits Business",
    "Brewbound",
    "VinePair",
    "Wine Business",
    "SevenFifty Daily",
    "Beverage Industry",
    "Beverage Daily",
    "Harpers Wine & Spirit",
    "Food Navigator",
    "Food Navigator USA",
    "Food Navigator Asia",
    "Wines of Argentina",
    "Coviar",
    # New global trade
    "BevNET",
    "Decanter",
    "Food Business News",
    "Craft Brewing Business",
    "Drinks International",
    "New Food Magazine",
    "Winsight Grocery Business",
    "Packaging World",
    "The Morning Advertiser",
    "Supermarket News",
    "The Grocer",
    "Progressive Grocer",
    "Convenience Store News",
    "Beer & Brewer",
    "The Canopy",
    "Meininger's Wine Business International",
    "Global Beer Network",
    "Wine Industry Advisor",
    "Brewers Journal",
    "Packaging Digest",
    "Bodegas de Argentina",
    "Vinos y Negocios",
    "Mundo Alimentario",
    "Mercado e Consumo",
    "Canal Food Brasil",
}
INDUSTRY_RELEVANCE_TERMS = {
    "resultados", "ventas", "ingresos", "ganancias", "facturacion",
    "mercado", "negocio", "empresa", "compania", "industria",
    "inversion", "adquisicion", "fusion", "planta", "produccion",
    "distribucion", "lanzamiento", "marca", "consumo", "precios",
    "exportaciones", "regulacion", "impuesto", "etiquetado", "retail",
    "canal", "earnings", "sales", "revenue", "profit", "market",
    "business", "company", "industry", "investment", "acquisition",
    "merger", "plant", "production", "distribution", "launch", "brand",
    "consumer", "pricing", "regulation", "tax", "labeling", "volume",
    "volumes", "category",
}
STRONG_INDUSTRY_RELEVANCE_TERMS = INDUSTRY_RELEVANCE_TERMS - {"consumo", "consumer"}
LOW_VALUE_TERMS = {
    "receta", "recetas", "salud", "hidratacion", "hidratan", "ciencia",
    "nutricion", "dieta", "medico", "medicos", "beneficios",
    "como preparar", "que pasa si", "curiosidad", "curiosidades",
    "horoscopo", "clima", "pronostico", "recipe", "recipes", "health",
    "hydration", "nutrition", "diet", "doctor", "benefits", "how to make",
}

# Patrones de paywall / boilerplate en el body
PAYWALL_PATTERNS = (
    "subscribe to continue",
    "subscribe to read",
    "sign in to read",
    "sign up to read",
    "create a free account",
    "already a subscriber",
    "to continue reading",
    "para continuar leyendo",
    "suscribite para",
    "regístrate para",
    "registrate para",
    "inicia sesión para",
    "this content is for subscribers",
    "this article is for paid subscribers",
    "access this article",
    "unlock this article",
    "read the full article",
    "lee el artículo completo",
    # Páginas de login / newsletter gate
    "sign in to your account",
    "log in to your account",
    "log in to your insider",
    "log in to your insider account",
    "if you're not an insider",
    "if you are not an insider",
    "view the full daily briefing",
    "continue to the free version",
    "enter your email",
    "enter your password",
    "forgot your password",
    "please log in",
    "please sign in",
    "inicia sesión",
    "ingresa tu contraseña",
    "ingresá tu contraseña",
)

# Títulos que son nombres de sección/newsletter, no de artículo
GENERIC_TITLES = {
    "google news",
    "daily briefing",
    "morning briefing",
    "weekly briefing",
    "news briefing",
    "just drinks",
    "beverage daily",
    "brewbound",
    "the drinks business",
    "food navigator",
    "foodnavigator",
    "just-drinks",
    "vinepair",
    "the spirits business",
    "harpers wine & spirit",
    "sevenfifty daily",
    "wine business",
    "bevindustry",
    "infobae",
    "clarin",
    "la nacion",
    "ambito",
    "cronista",
    "iprofesional",
    "perfil",
}


def _is_generic_title(title, source):
    lowered = title.strip().lower()
    if lowered in GENERIC_TITLES:
        return True
    # Título igual al nombre de la fuente
    if source and lowered == source.strip().lower():
        return True
    # Título que es solo el nombre de la fuente seguido de separador
    for generic in GENERIC_TITLES:
        if lowered.startswith(generic + " -") or lowered.startswith(generic + " |"):
            return True
    return False


def _is_truncated_title(title):
    stripped = title.strip()
    # Termina con puntos suspensivos o guion (título cortado por el feed)
    return stripped.endswith("...") or stripped.endswith("…") or re.search(r'\s[-–]\s*$', stripped) is not None


def _has_paywall_body(body):
    lowered = body.lower()
    # Si el body es muy corto Y contiene señal de paywall
    if len(body) < 600:
        return any(pattern in lowered for pattern in PAYWALL_PATTERNS)
    # Body largo pero abre directo con paywall
    first_300 = lowered[:300]
    return any(pattern in first_300 for pattern in PAYWALL_PATTERNS)


def _is_repetitive_body(body):
    # Detecta body que repite el mismo fragmento (scraping fallido)
    if len(body) < 200:
        return False
    sentences = [s.strip() for s in re.split(r'[.!?]\s+', body) if len(s.strip()) > 30]
    if len(sentences) < 4:
        return False
    unique = set(s.lower() for s in sentences)
    # Si más del 60% de las oraciones son duplicadas, es repetitivo
    return (len(sentences) - len(unique)) / len(sentences) > 0.6


def _has_industry_relevance(article, title, summary, body):
    if article.companies:
        return True
    if article.source in TRADE_SOURCES:
        return True

    text = " ".join([title, summary, body[:1500]])
    low_value = any(term_in_text(term, text) for term in LOW_VALUE_TERMS)
    strategic_topics = {
        "financial_results",
        "ma_and_strategy",
        "distribution_execution",
        "regulation_tax_policy",
        "risk_crisis_reputation",
        "packaging_sustainability",
        "supply_chain_commodities",
    }
    if set(article.segments or []) & strategic_topics and not low_value:
        return True

    if low_value:
        return any(term_in_text(term, text) for term in STRONG_INDUSTRY_RELEVANCE_TERMS)

    if any(term_in_text(term, text) for term in INDUSTRY_RELEVANCE_TERMS):
        return True

    return False


_HISTORICAL_BODY_PATTERNS = (
    re.compile(r"\bhace\s+\d{2,3}\s+a[ñn]os\b", re.IGNORECASE),
    re.compile(r"\b(fundad[ao]|nacid[ao])\s+en\s+(19\d{2}|18\d{2}|20[01]\d)\b", re.IGNORECASE),
    re.compile(r"\b(en|del?)\s+el\s+(siglo|a[ñn]o)\s+(xix|xx|19\d{2}|18\d{2})\b", re.IGNORECASE),
    re.compile(r"\b(comenz[oó]|empez[oó]|inici[oó])\s+(su\s+(historia|trayectoria)|en\s+(19|18)\d{2})", re.IGNORECASE),
    re.compile(r"\b(more than|over|hace m[aá]s de)\s+\d{2,3}\s+(years|a[ñn]os)\s+ago\b", re.IGNORECASE),
    re.compile(r"\b(the (origin|history|story)|origen|historia)\s+of\b", re.IGNORECASE),
)
_RECENT_NEWS_PATTERNS = (
    re.compile(r"\b(anunci[oó]|anunci[oó]\s+hoy|esta\s+semana|este\s+a[ñn]o|este\s+mes|inform[oó])\b", re.IGNORECASE),
    re.compile(r"\b(announced|reports?|reported|today|this\s+week|this\s+month|q[1-4]\s+20\d{2})\b", re.IGNORECASE),
    re.compile(r"\b(resultados|ventas|ingresos|facturaci[oó]n|earnings|revenue)\b", re.IGNORECASE),
)


def _is_historical_body(body):
    if not body or len(body) < 200:
        return False
    sample = body[:2000]
    historical_hits = sum(1 for p in _HISTORICAL_BODY_PATTERNS if p.search(sample))
    if historical_hits < 2:
        return False
    recent_hits = sum(1 for p in _RECENT_NEWS_PATTERNS if p.search(sample))
    # >=2 marcadores historicos y casi ningun marcador de actualidad -> es nota historica
    return recent_hits == 0


# Reject articles whose URL was never resolved from Google News
_GOOGLE_NEWS_URL_PATTERN = re.compile(r"https?://(news\.google\.com|www\.google\.com/url)", re.IGNORECASE)

# Patterns in the TITLE that flag sensational/non-executive content
# (only applied to non-trade, non-company-match articles)
_SENSATIONAL_TITLE_PATTERNS = (
    re.compile(r"\b(serpiente|culebra|alacrán|escorpión|insecto|cucaracha|rata|ratón|roedor|animal)\b", re.IGNORECASE),
    re.compile(r"\b(video[:\s]|viral|mira\s+c[oó]mo|impactante|increíble|sorprendente|descubrieron)\b", re.IGNORECASE),
    re.compile(r"\bcanasta\s+(de\s+)?(precios|básica|2025|2026)\b", re.IGNORECASE),
    re.compile(r"\b(oferta|descuento|precio\s+hist[oó]rico|liquidaci[oó]n)\b", re.IGNORECASE),
    re.compile(r"\b(receta|c[oó]mo\s+preparar|cu[aá]nto\s+cuesta|por\s+qu[eé]\s+tomar|beneficios\s+de\s+tomar)\b", re.IGNORECASE),
)


def validate_article(article):
    """
    Valida un artículo post-extracción.
    Retorna (True, None) si es válido, o (False, razón) si debe descartarse.
    """
    title = (article.title or "").strip()
    body = (article.body or "").strip()
    summary = (article.summary or "").strip()

    if not title:
        return False, "empty_title"

    # If URL was never resolved from Google News, only reject if content is also insufficient.
    # If extraction got good title+body, keep it — Google News may be the only source.
    if _GOOGLE_NEWS_URL_PATTERN.match(article.url or ""):
        if len(body) < 150 and len(summary) < 60:
            return False, "unresolved_google_news_url"

    # Reject sensational/non-executive content unless it's a trade source or has a company match
    is_protected = article.source in TRADE_SOURCES or bool(article.companies)
    if not is_protected:
        for pattern in _SENSATIONAL_TITLE_PATTERNS:
            if pattern.search(title):
                return False, f"sensational_non_executive:{title[:60]!r}"

    # Doble check de antigüedad post-extracción: el candidato puede haber pasado
    # el filtro de 36h en filtering.py con published="" (listings de sección sin
    # fecha visible). Acá ya tenemos el published real de JSON-LD/OG.
    published = (getattr(article, "published", "") or "").strip()
    if published:
        try:
            parsed = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)
            if age > timedelta(hours=MAX_AGE_HOURS_POST_EXTRACTION):
                return False, f"too_old_post_extraction({int(age.total_seconds() // 3600)}h)"
        except (ValueError, TypeError):
            pass
    else:
        return False, "missing_publish_date"

    if len(title) < MIN_TITLE_LEN:
        return False, f"title_too_short({len(title)})"

    if len(title) > MAX_TITLE_LEN:
        return False, f"title_too_long({len(title)})"

    if _is_generic_title(title, article.source):
        return False, f"generic_title:{title[:50]!r}"

    if _is_truncated_title(title):
        return False, f"truncated_title:{title[:60]!r}"

    if _has_paywall_body(body):
        return False, "paywall_body"

    if _is_repetitive_body(body):
        return False, "repetitive_body"

    if _is_historical_body(body):
        return False, "historical_body"

    if not _has_industry_relevance(article, title, summary, body):
        return False, "not_industry_relevant"

    return True, None


def validate_articles(articles):
    """
    Filtra artículos inválidos. Retorna (válidos, diagnósticos).
    """
    valid = []
    rejected = []

    for article in articles:
        ok, reason = validate_article(article)
        if ok:
            valid.append(article)
        else:
            rejected.append({"url": article.url, "title": article.title, "reason": reason})
            logger.warning("Validation rejected: [%s] %r — %s", article.source, article.title[:60], reason)

    logger.info(
        "Validation: %d valid / %d rejected out of %d articles",
        len(valid), len(rejected), len(articles),
    )
    by_reason = {}
    for item in rejected:
        by_reason[item["reason"]] = by_reason.get(item["reason"], 0) + 1
    return valid, {"valid": len(valid), "rejected": len(rejected), "by_reason": by_reason, "rejections": rejected}
