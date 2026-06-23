import ipaddress
import logging
import time
from urllib.parse import urlsplit

import requests


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.8,es;q=0.7,pt;q=0.6",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)

logger = logging.getLogger(__name__)

# Hard cap on response body to avoid memory exhaustion from a hostile/huge page.
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB


def _is_safe_url(url):
    """Reject SSRF-prone targets before issuing a request.

    Only http/https is allowed, and literal private/loopback/link-local IP hosts
    are blocked (e.g. 127.0.0.1, 169.254.169.254 cloud metadata, 10.x, 192.168.x).
    Legitimate news sources are domain-based and unaffected.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    host = parts.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return True  # hostname (not a literal IP) — allowed
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved)


def fetch_text(url, timeout=4, retries=2):
    if not _is_safe_url(url):
        return "", "blocked_unsafe_url"
    last_status = ""
    for attempt in range(retries + 1):
        try:
            response = SESSION.get(url, timeout=timeout)
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit() and int(content_length) > MAX_RESPONSE_BYTES:
                return "", "too_large"
            if response.status_code != 200:
                return "", f"http_{response.status_code}"
            if not response.encoding or response.encoding.lower() in {"iso-8859-1", "latin-1"}:
                response.encoding = response.apparent_encoding or "utf-8"
            return response.text, "ok"
        except requests.exceptions.Timeout:
            last_status = "timeout"
            logger.debug("Timeout on attempt %d/%d: %s", attempt + 1, retries + 1, url)
        except requests.exceptions.RequestException as exc:
            return "", f"request_error:{exc.__class__.__name__}"
        if attempt < retries:
            delay = 2 ** attempt  # 1s, 2s, 4s…
            time.sleep(delay)
    return "", last_status
