import re
from typing import Dict, List, Tuple
from urllib.parse import urlparse, urlunparse

from src.models import CreatorCandidate


def normalize_profile_url(url: str) -> str:
    try:
        p = urlparse((url or "").strip())
    except Exception:  # noqa: BLE001
        return (url or "").lower().rstrip("/")
    host = (p.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    path = re.sub(r"/+$", "", p.path or "")
    return urlunparse((p.scheme or "https", host, path, "", "", ""))


def _completeness_score(c: CreatorCandidate) -> Tuple[int, int, int, int, int]:
    return (
        1 if c.follower_count is not None else 0,
        1 if c.bio and len(c.bio) > 10 else 0,
        1 if c.recent_posts else 0,
        1 if c.avg_likes is not None or c.engagement_rate is not None else 0,
        1 if c.profile_url else 0,
    )


def _better(a: CreatorCandidate, b: CreatorCandidate) -> CreatorCandidate:
    sa = _completeness_score(a)
    sb = _completeness_score(b)
    if sa != sb:
        return a if sa > sb else b
    return a if (a.fit_score or 0) >= (b.fit_score or 0) else b


def _primary_key(c: CreatorCandidate) -> str:
    if c.profile_url:
        return normalize_profile_url(c.profile_url)
    if c.handle:
        h = c.handle.lstrip("@").lower()
        return f"{c.platform.lower()}|{h}"
    return f"name|{(c.name or '').strip().lower()[:120]}"


def dedupe_creators(creators: List[CreatorCandidate]) -> List[CreatorCandidate]:
    """
    Deduplicate by normalized profile URL, platform+handle, or name (BUILD §16).
    """
    buckets: Dict[str, CreatorCandidate] = {}
    for c in creators:
        k = _primary_key(c)
        if k in buckets:
            buckets[k] = _better(c, buckets[k])
        else:
            buckets[k] = c
    return list(buckets.values())
