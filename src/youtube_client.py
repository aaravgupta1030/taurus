import logging
import re
import statistics
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests

from src.engagement_metrics import apply_preferred_engagement_rate, is_valid_enriched_profile
from src.models import CreatorCandidate
from src.utils import get_env, load_settings, log_error, retry_http, is_retryable_http_error

logger = logging.getLogger(__name__)


def _youtube_error_detail(resp: Optional[requests.Response]) -> str:
    if resp is None:
        return ""
    try:
        body = resp.json()
        err = body.get("error") or {}
        msg = err.get("message", "")
        errs = err.get("errors") or []
        reasons = [e.get("reason", "") for e in errs if isinstance(e, dict)]
        return f"{msg} reasons={reasons}" if reasons else str(msg or body)[:400]
    except Exception:  # noqa: BLE001
        return (resp.text or "")[:400]


def _yt_get(path: str, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    key = get_env("YOUTUBE_API_KEY")
    if not key:
        log_error("YouTube: YOUTUBE_API_KEY missing")
        return None
    settings = load_settings()
    timeout = settings.get("http_timeout_seconds", 45)
    retry_cfg = settings.get("retry", {})
    full_params = {"key": key, **params}

    def do_req() -> requests.Response:
        r = requests.get(
            f"https://www.googleapis.com/youtube/v3/{path}",
            params=full_params,
            timeout=timeout,
        )
        r.raise_for_status()
        return r

    try:
        resp = retry_http(
            do_req,
            max_attempts=retry_cfg.get("max_attempts", 4),
            base_delay=retry_cfg.get("base_delay_seconds", 1.0),
            max_delay=retry_cfg.get("max_delay_seconds", 30.0),
            retry_on=lambda e: is_retryable_http_error(e),
        )
        return resp.json()
    except requests.HTTPError as e:
        detail = _youtube_error_detail(e.response)
        code = e.response.status_code if e.response is not None else "?"
        log_error(
            f"YouTube API HTTP {code} {path} {params}: {detail or e}. "
            "If 403: enable YouTube Data API v3 on the key’s Google Cloud project, "
            "check quota, and remove overly strict API key application restrictions for server use."
        )
        return None
    except Exception as e:  # noqa: BLE001
        log_error(f"YouTube API error {path} {params}: {e}")
        return None


def _parse_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _fetch_videos_statistics(video_ids: List[str]) -> Dict[str, Dict[str, Optional[int]]]:
    """Batch videos.list (statistics) — up to 50 ids per call."""
    out: Dict[str, Dict[str, Optional[int]]] = {}
    # Dedupe while preserving order
    seen = set()
    uniq: List[str] = []
    for vid in video_ids:
        if vid and vid not in seen:
            seen.add(vid)
            uniq.append(vid)
    for i in range(0, len(uniq), 50):
        chunk = uniq[i : i + 50]
        data = _yt_get("videos", {"part": "statistics", "id": ",".join(chunk)})
        if not data:
            continue
        for item in data.get("items") or []:
            vid = item.get("id")
            st = item.get("statistics") or {}
            if not vid:
                continue
            out[vid] = {
                "likeCount": _parse_int(st.get("likeCount")),
                "commentCount": _parse_int(st.get("commentCount")),
                "viewCount": _parse_int(st.get("viewCount")),
            }
    return out


def _extract_video_id(url: str) -> Optional[str]:
    u = urlparse(url)
    if u.netloc in ("youtu.be", "www.youtu.be"):
        return (u.path or "").strip("/").split("/")[0] or None
    q = parse_qs(u.query)
    if "v" in q:
        return q["v"][0]
    m = re.search(r"youtube\.com/shorts/([^/?#]+)", url, re.I)
    if m:
        return m.group(1)
    m = re.search(r"youtube\.com/live/([^/?#]+)", url, re.I)
    if m:
        return m.group(1)
    return None


def _extract_channel_id_from_url(url: str) -> Optional[str]:
    m = re.search(r"youtube\.com/channel/([^/?#]+)", url, re.I)
    if m:
        return m.group(1)
    return None


def _extract_handle_from_youtube_url(url: str) -> Optional[str]:
    m = re.search(r"youtube\.com/@([^/?#]+)", url, re.I)
    if m:
        return m.group(1)
    return None


def _resolve_channel_id(url: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (channel_id, resolved_handle_without_at)."""
    vid = _extract_video_id(url)
    if vid:
        data = _yt_get("videos", {"part": "snippet,statistics", "id": vid})
        items = (data or {}).get("items") or []
        if not items:
            return None, None
        ch = (items[0].get("snippet") or {}).get("channelId")
        return ch, None

    cid = _extract_channel_id_from_url(url)
    if cid:
        return cid, None

    handle = _extract_handle_from_youtube_url(url)
    if handle:
        data = _yt_get("channels", {"part": "id", "forHandle": handle})
        items = (data or {}).get("items") or []
        if items:
            return items[0].get("id"), handle
        # fallback search
        data = _yt_get(
            "search",
            {"part": "snippet", "type": "channel", "q": handle, "maxResults": 1},
        )
        items = (data or {}).get("items") or []
        if items:
            return (items[0].get("snippet") or {}).get("channelId"), handle
        return None, handle

    # /c/name or /user/name — search
    m = re.search(r"youtube\.com/(?:c|user)/([^/?#]+)", url, re.I)
    if m:
        qterm = m.group(1)
        data = _yt_get(
            "search",
            {"part": "snippet", "type": "channel", "q": qterm, "maxResults": 3},
        )
        items = (data or {}).get("items") or []
        for it in items:
            cid = (it.get("snippet") or {}).get("channelId")
            if cid:
                return cid, None

    return None, None


def enrich_youtube_creator(url: str, source_url: str) -> Optional[CreatorCandidate]:
    """Fetch channel metadata, recent uploads, and per-video stats for engagement (likes/comments)."""
    ch_id, handle_hint = _resolve_channel_id(url)
    if not ch_id:
        log_error(f"YouTube: could not resolve channel id for {url}")
        return None

    ch_data = _yt_get(
        "channels",
        {"part": "snippet,statistics,contentDetails", "id": ch_id},
    )
    items = (ch_data or {}).get("items") or []
    if not items:
        return None
    ch = items[0]
    snippet = ch.get("snippet") or {}
    stats = ch.get("statistics") or {}
    content = ch.get("contentDetails") or {}
    title = snippet.get("title") or "Unknown"
    desc = snippet.get("description") or ""
    custom_url = snippet.get("customUrl") or ""
    subs_raw = stats.get("subscriberCount")
    try:
        follower_count = int(subs_raw) if subs_raw is not None else None
    except (TypeError, ValueError):
        follower_count = None

    handle = None
    if custom_url:
        handle = f"@{custom_url.lstrip('@')}"
    elif handle_hint:
        handle = f"@{handle_hint}"
    else:
        handle = f"@{title.replace(' ', '')[:40]}"

    profile_url = f"https://www.youtube.com/channel/{ch_id}"
    if custom_url:
        profile_url = f"https://www.youtube.com/@{custom_url.lstrip('@')}"

    uploads = (content.get("relatedPlaylists") or {}).get("uploads")
    recent_posts: List[Dict[str, Any]] = []
    video_ids: List[str] = []

    if uploads:
        settings = load_settings()
        lim = int(settings.get("youtube", {}).get("recent_videos_limit", 5))
        pl = _yt_get(
            "playlistItems",
            {
                "part": "snippet,contentDetails",
                "playlistId": uploads,
                "maxResults": lim,
            },
        )
        for it in (pl or {}).get("items") or []:
            sn = it.get("snippet") or {}
            rid = sn.get("resourceId") or {}
            vid_id = rid.get("videoId")
            if vid_id:
                video_ids.append(vid_id)
            recent_posts.append(
                {
                    "video_id": vid_id,
                    "title": sn.get("title"),
                    "description": (sn.get("description") or "")[:500],
                }
            )

    stats_by_video = _fetch_videos_statistics(video_ids)
    likes_vals: List[float] = []
    comments_vals: List[float] = []

    for post in recent_posts:
        vid = post.get("video_id")
        if not vid:
            continue
        st = stats_by_video.get(vid) or {}
        lc = st.get("likeCount")
        cc = st.get("commentCount")
        vc = st.get("viewCount")
        post["likes"] = lc
        post["comments"] = cc
        post["views"] = vc
        if lc is not None:
            likes_vals.append(float(lc))
        if cc is not None:
            comments_vals.append(float(cc))

    if len(likes_vals) >= 2:
        avg_likes = float(statistics.median(likes_vals))
    else:
        avg_likes = sum(likes_vals) / len(likes_vals) if likes_vals else None
    if len(comments_vals) >= 2:
        avg_comments = float(statistics.median(comments_vals))
    else:
        avg_comments = sum(comments_vals) / len(comments_vals) if comments_vals else None

    summary_bits = []
    for p in recent_posts[:3]:
        if p.get("title"):
            summary_bits.append(str(p["title"]))
    recent_summary = "; ".join(summary_bits) if summary_bits else (desc[:280] if desc else title)

    cand = CreatorCandidate(
        name=title,
        platform="YouTube",
        handle=handle,
        profile_url=profile_url,
        bio=desc[:2000],
        follower_count=follower_count,
        recent_content_summary=recent_summary,
        source_url=source_url,
        recent_posts=recent_posts,
        avg_likes=avg_likes,
        avg_comments=avg_comments,
        engagement_rate=None,
    )
    apply_preferred_engagement_rate(cand)
    if not is_valid_enriched_profile(cand):
        return None
    return cand
