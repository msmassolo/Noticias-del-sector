import html
import re
import unicodedata


def clean_text(value):
    value = html.unescape(value or "")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def normalize_text(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def term_in_text(term, text):
    normalized_term = normalize_text(term)
    normalized_text = normalize_text(text)
    if not normalized_term or not normalized_text:
        return False

    pattern = rf"(^|\s){re.escape(normalized_term)}($|\s)"
    return re.search(pattern, normalized_text) is not None


def natural_trim(value, max_chars):
    value = clean_text(value)
    if len(value) <= max_chars:
        return value

    trimmed = value[:max_chars].rsplit(" ", 1)[0].strip()
    last_stop = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
    if last_stop >= int(max_chars * 0.55):
        return trimmed[: last_stop + 1].strip()
    return trimmed
