import re
from typing import Optional
from urllib.parse import unquote, urlparse


def detect_platform(url: str) -> Optional[str]:
    u = url.lower()
    if "tiktok.com/@" in u or "tiktok.com%2f%40" in u:
        return "TikTok"
    if "tiktok.com" in u and "/@" in unquote(u):
        return "TikTok"
    if "instagram.com" in u:
        return "Instagram"
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube"
    return None


def extract_handle_from_url(url: str, platform: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        path = unquote(parsed.path or "")
    except Exception:  # noqa: BLE001
        return None

    if platform == "TikTok":
        m = re.search(r"@([^/?#]+)", url)
        if m:
            return f"@{m.group(1)}"
        return None

    if platform == "Instagram":
        if "/p/" in path or "/reel/" in path or "/tv/" in path or "/stories/" in path:
            return None
        if path.startswith("/explore/") or path.startswith("/locations/"):
            return None
        parts = [p for p in path.split("/") if p]
        if not parts:
            return None
        user = parts[0]
        if user in {"explore", "reel", "reels", "p", "stories", "tv"}:
            return None
        return f"@{user}"

    if platform == "YouTube":
        m = re.search(r"youtube\.com/@([^/?#]+)", url, re.I)
        if m:
            return f"@{m.group(1)}"
        m = re.search(r"youtube\.com/c/([^/?#]+)", url, re.I)
        if m:
            return f"@{m.group(1)}"
        m = re.search(r"youtube\.com/user/([^/?#]+)", url, re.I)
        if m:
            return f"@{m.group(1)}"
        # /channel/UC... — no @handle in URL; caller uses channel id resolution
        return None

    return None


def profile_url_from_parts(platform: str, handle: str) -> str:
    h = handle.lstrip("@").strip()
    if platform == "TikTok":
        return f"https://www.tiktok.com/@{h}"
    if platform == "Instagram":
        return f"https://www.instagram.com/{h}/"
    if platform == "YouTube":
        return f"https://www.youtube.com/@{h}"
    return f"https://{h}"


def is_likely_non_creator_page(url: str, platform: Optional[str]) -> bool:
    u = url.lower()
    if any(
        x in u
        for x in (
            "/hashtag/",
            "/tags/",
            "/search",
            "google.com",
            "facebook.com",
            "amazon.",
            "ebay.",
            "/marketplace",
            "/shop/",
            "/store/",
        )
    ):
        return True
    if platform == "Instagram" and ("/explore/" in u or "/locations/" in u):
        return True
    if platform == "YouTube" and ("/results?" in u or "/feed/" in u):
        return True
    return False
