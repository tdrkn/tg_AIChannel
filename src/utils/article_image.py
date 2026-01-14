import logging
import re
from typing import Optional
from urllib.parse import urljoin

import httpx

logger = logging.getLogger(__name__)


_META_KEYS = [
    # OpenGraph
    ("property", "og:image"),
    ("property", "og:image:secure_url"),
    # Twitter cards
    ("name", "twitter:image"),
    ("name", "twitter:image:src"),
]


def _extract_meta_image_url(html: str) -> Optional[str]:
    """Extract best-effort image URL from meta tags."""
    if not html:
        return None

    # Very small HTML "parser" via regex (sufficient for meta tags)
    # Accept any attribute order and both quote styles.
    for attr, key in _META_KEYS:
        pattern = re.compile(
            rf"<meta[^>]*\b{attr}\s*=\s*(['\"])\s*{re.escape(key)}\s*\1[^>]*>",
            re.IGNORECASE,
        )
        for m in pattern.finditer(html):
            tag = m.group(0)
            content_match = re.search(r"\bcontent\s*=\s*(['\"])(.*?)\1", tag, re.IGNORECASE)
            if content_match:
                candidate = (content_match.group(2) or "").strip()
                if candidate:
                    return candidate

    # Sometimes sites use <link rel="image_src" href="...">
    link_match = re.search(
        r"<link[^>]*\brel\s*=\s*(['\"])\s*image_src\s*\1[^>]*>",
        html,
        re.IGNORECASE,
    )
    if link_match:
        tag = link_match.group(0)
        href_match = re.search(r"\bhref\s*=\s*(['\"])(.*?)\1", tag, re.IGNORECASE)
        if href_match:
            candidate = (href_match.group(2) or "").strip()
            if candidate:
                return candidate

    return None


async def fetch_article_image_url(
    url: str,
    timeout_sec: float = 10,
    max_bytes: int = 512_000,
) -> Optional[str]:
    """Fetch article HTML and try to extract an image URL (og:image / twitter:image).

    Returns an absolute URL or None.
    """
    if not url:
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; tg_AIChannel/1.0; +https://t.me/yanochka_news)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout_sec, headers=headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = (resp.headers.get("content-type") or "").lower()
            if "text/html" not in content_type and "application/xhtml" not in content_type:
                logger.info("Article image: non-HTML content-type=%s url=%s", content_type, url)
                return None

            text = resp.text
            if len(text) > max_bytes:
                text = text[:max_bytes]

    except Exception as e:
        logger.warning("Article image fetch failed for %s: %s", url, e)
        return None

    raw = _extract_meta_image_url(text)
    if not raw:
        return None

    # Make absolute if needed
    abs_url = urljoin(url, raw)
    if abs_url.startswith("http://") or abs_url.startswith("https://"):
        return abs_url

    return None
