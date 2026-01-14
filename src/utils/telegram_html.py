import html
import re
from typing import Optional

# Telegram HTML supports a limited set of tags.
_ALLOWED_TAGS = {
    "b",
    "strong",
    "i",
    "em",
    "u",
    "ins",
    "s",
    "strike",
    "del",
    "code",
    "pre",
}


def sanitize_telegram_html(text: str) -> str:
    """Best-effort sanitizer for Telegram parse_mode=HTML.

    - Removes <a> tags from the LLM body (we append our own footer separately)
    - Removes unsupported tags
    - Normalizes <br> to newlines

    This is not a full HTML parser, but it prevents the most common Telegram errors.
    """
    t = (text or "").strip()
    if not t:
        return ""

    # Normalize line breaks
    t = re.sub(r"<\s*br\s*/?\s*>", "\n", t, flags=re.IGNORECASE)

    # Strip all anchors (Telegram is picky about href formatting)
    t = re.sub(r"<\s*a\b[^>]*>", "", t, flags=re.IGNORECASE)
    t = re.sub(r"<\s*/\s*a\s*>", "", t, flags=re.IGNORECASE)

    # Remove any tag that isn't allowed.
    def _strip_unsupported(match: re.Match[str]) -> str:
        tag = match.group(1) or ""
        tag_name = tag.strip().lstrip("/").split(None, 1)[0].lower()
        if tag_name in _ALLOWED_TAGS:
            return match.group(0)
        return ""

    t = re.sub(r"<\s*([^>]+)\s*>", _strip_unsupported, t)

    # Collapse excessive newlines/spaces
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r"[ \t]{2,}", " ", t)

    return t.strip()


def build_footer(source_url: Optional[str], channel_url: Optional[str]) -> str:
    parts = []
    if source_url:
        parts.append(f"<a href=\"{html.escape(source_url, quote=True)}\">источник</a>")
    if channel_url:
        parts.append(f"<a href=\"{html.escape(channel_url, quote=True)}\">⚡️яночка ньюс⚡️</a>")
    return "\n".join(parts).strip()


def append_footer(text: str, source_url: Optional[str], channel_url: Optional[str]) -> str:
    body = (text or "").strip()
    footer = build_footer(source_url, channel_url)
    if not footer:
        return body

    # Remove old footer-ish lines to avoid duplication.
    lines = [ln.rstrip() for ln in body.splitlines()]
    cleaned = []
    for ln in lines:
        low = ln.lower()
        if "t.me/" in low or "⚡️яночка" in ln.lower() or "источник" in low or "источн" in low:
            continue
        cleaned.append(ln)

    body_clean = "\n".join(cleaned).strip()
    if not body_clean:
        return footer

    return f"{body_clean}\n\n{footer}"


def channel_id_to_url(channel_id: Optional[str]) -> Optional[str]:
    if not channel_id:
        return None
    c = channel_id.strip()
    if c.startswith("@"):  # @name
        return f"https://t.me/{c[1:]}"
    if c.startswith("https://t.me/") or c.startswith("http://t.me/"):
        return c
    return None
