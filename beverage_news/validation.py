import logging
import re

logger = logging.getLogger(__name__)

MIN_TITLE_LEN = 15
MIN_SUMMARY_LEN = 30
MAX_TITLE_LEN = 250

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
    if len(body) < 400:
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
    return valid, {"valid": len(valid), "rejected": len(rejected), "rejections": rejected}
