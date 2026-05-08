from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit


TRACKING_KEYS = {"fbclid", "gclid", "igshid"}
TRACKING_PREFIXES = ("utm_",)


def normalize_url(base_url, link):
    if not link:
        return ""

    url = urljoin(base_url, link.strip())
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return ""

    query = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in TRACKING_KEYS and not key.startswith(TRACKING_PREFIXES)
    ]
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path, urlencode(query, doseq=True), ""))


def domain_of(url):
    return urlsplit(url).netloc.lower().removeprefix("www.")
