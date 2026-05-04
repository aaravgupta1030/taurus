import logging
import re
import statistics
import time
from typing import Any, Dict, List, Optional

import requests

from src.engagement_metrics import apply_preferred_engagement_rate, is_valid_enriched_profile, post_views
from src.models import CreatorCandidate
from src.platform_router import extract_handle_from_url, profile_url_from_parts
from src.utils import get_env, load_settings, log_error, retry_http, is_retryable_http_error

logger = logging.getLogger(__name__)


def _actor_slug_for_url(actor_id: str) -> str:
    return actor_id.replace("/", "~")


def _run_apify_actor(actor_id: str, run_input: Dict[str, Any]) -> List[Dict[str, Any]]:
    token = get_env("APIFY_API_TOKEN")
    if not token:
        log_error("Apify: APIFY_API_TOKEN missing")
        return []

    settings = load_settings()
    apify_cfg = settings.get("apify", {})
    max_wait = int(apify_cfg.get("max_wait_seconds", 300))
    poll = float(apify_cfg.get("poll_interval_seconds", 5))
    timeout = settings.get("http_timeout_seconds", 45)
    retry_cfg = settings.get("retry", {})

    slug = _actor_slug_for_url(actor_id)
    run_url = f"https://api.apify.com/v2/acts/{slug}/runs"

    def start_run() -> requests.Response:
        r = requests.post(
            run_url,
            params={"token": token},
            json=run_input,
            timeout=timeout,
        )
        r.raise_for_status()
        return r

    try:
        started = retry_http(
            start_run,
            max_attempts=retry_cfg.get("max_attempts", 4),
            base_delay=retry_cfg.get("base_delay_seconds", 1.0),
            max_delay=retry_cfg.get("max_delay_seconds", 30.0),
            retry_on=lambda e: is_retryable_http_error(e),
        )
    except Exception as e:  # noqa: BLE001
        hint = ""
        err_s = str(e).lower()
        if "403" in err_s or "forbidden" in err_s:
            hint = (
                " — Apify returned 403: open https://console.apify.com , confirm **Integration** "
                "API token in .env as APIFY_API_TOKEN, billing/credits, and that your plan can run "
                "this actor (some actors are paid or restricted)."
            )
        log_error(f"Apify start run failed {actor_id}: {e}{hint}")
        return []

    run_info = started.json().get("data") or {}
    run_id = run_info.get("id")
    if not run_id:
        log_error(f"Apify: no run id in response for {actor_id}")
        return []

    status_url = f"https://api.apify.com/v2/actor-runs/{run_id}"
    waited = 0.0
    r: Optional[requests.Response] = None
    while waited < max_wait:
        r = requests.get(status_url, params={"token": token}, timeout=timeout)
        r.raise_for_status()
        st = (r.json().get("data") or {}).get("status")
        if st in ("SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"):
            if st != "SUCCEEDED":
                log_error(f"Apify run {run_id} ended with {st}")
            break
        time.sleep(poll)
        waited += poll

    if r is None:
        return []

    final = (r.json().get("data") or {})
    if final.get("status") != "SUCCEEDED":
        return []

    dataset_id = final.get("defaultDatasetId")
    if not dataset_id:
        return []

    items_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items"
    r = requests.get(items_url, params={"token": token}, timeout=timeout)
    r.raise_for_status()
    data = r.json()
    return data if isinstance(data, list) else []


def _flat_strings(obj: Any, out: Optional[List[str]] = None) -> List[str]:
    if out is None:
        out = []
    if isinstance(obj, str):
        if len(obj) > 2:
            out.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            _flat_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _flat_strings(v, out)
    return out


def _first_int(*keys: str, root: Dict[str, Any]) -> Optional[int]:
    def walk(d: Any) -> Optional[int]:
        if isinstance(d, dict):
            for k, v in d.items():
                lk = str(k).lower()
                if any(x in lk for x in keys):
                    if isinstance(v, (int, float)):
                        return int(v)
                    if isinstance(v, str) and v.isdigit():
                        return int(v)
                found = walk(v)
                if found is not None:
                    return found
        elif isinstance(d, list):
            for it in d:
                found = walk(it)
                if found is not None:
                    return found
        return None

    return walk(root)


_TIKTOK_NESTED_METRIC_KEYS = frozenset(
    {
        "diggCount",
        "diggedCount",
        "likeCount",
        "likes",
        "commentCount",
        "comments",
        "shareCount",
        "playCount",
        "viewCount",
        "collectCount",
        "forwardCount",
    }
)


def _parse_social_metric(val: Any) -> Optional[float]:
    """Parse TikTok-style counts: int, float, or strings like '12.5K' / '1.2M'."""
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        if isinstance(val, float) and val != val:
            return None
        return float(val)
    if isinstance(val, str):
        s = val.strip().lower().replace(",", "").replace(" ", "")
        if not s or s == "null":
            return None
        mult = 1.0
        if s.endswith("k"):
            mult, s = 1e3, s[:-1]
        elif s.endswith("m"):
            mult, s = 1e6, s[:-1]
        elif s.endswith("b"):
            mult, s = 1e9, s[:-1]
        try:
            return float(s) * mult
        except ValueError:
            return None
    return None


def _tiktok_flatten_item(it: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clockworks / TikTok payloads often put diggCount, playCount, etc. under stats
    or video — merge so post_views / likes read the real numbers.
    """
    out: Dict[str, Any] = dict(it)
    for nest in ("stats", "itemStats", "videoStats", "video", "videoMeta"):
        blk = it.get(nest)
        if not isinstance(blk, dict):
            continue
        for k, v in blk.items():
            if k not in _TIKTOK_NESTED_METRIC_KEYS:
                continue
            if v is None or v == "":
                continue
            cur = out.get(k)
            nv = _parse_social_metric(v)
            cv = _parse_social_metric(cur)
            if nv is None:
                continue
            if cv is None or cv == 0 or nv > cv:
                out[k] = v
    return out


def _tiktok_likes_from_flat(flat: Dict[str, Any]) -> Optional[float]:
    for key in ("diggCount", "diggedCount", "likeCount", "likes", "digg"):
        n = _parse_social_metric(flat.get(key))
        if n is not None and n >= 0:
            return n
    return None


def _tiktok_comments_from_flat(flat: Dict[str, Any]) -> Optional[float]:
    for key in ("commentCount", "comments", "replyCommentTotal", "comment"):
        n = _parse_social_metric(flat.get(key))
        if n is not None and n >= 0:
            return n
    return None


def _tiktok_items_to_candidate(
    items: List[Dict[str, Any]],
    profile_url: str,
    handle: str,
    source_url: str,
) -> Optional[CreatorCandidate]:
    if not items:
        return None
    root = items[0]
    # Clockworks / similar: authorMeta at top or nested
    author = root.get("authorMeta") or root.get("author") or root
    name = (
        author.get("nickName")
        or author.get("nickname")
        or author.get("name")
        or handle.lstrip("@")
    )
    bio = (
        author.get("signature")
        or author.get("bio")
        or root.get("signature")
        or ""
    )
    followers = _first_int("fan", "follower", "fans", root=author) or _first_int(
        "fan", "follower", "fans", root=root
    )

    posts: List[Dict[str, Any]] = []
    for it in items[:22]:
        if not isinstance(it, dict):
            continue
        flat = _tiktok_flatten_item(it)
        cap = flat.get("text") or flat.get("desc") or flat.get("description")
        row: Dict[str, Any] = {}
        if cap:
            row["caption"] = str(cap)[:500]
        digg_f = _tiktok_likes_from_flat(flat)
        comm_f = _tiktok_comments_from_flat(flat)
        if digg_f is not None:
            row["likes"] = digg_f
        if comm_f is not None:
            row["comments"] = comm_f
        pv = post_views(flat)
        if pv is not None:
            row["views"] = pv
        if row:
            posts.append(row)

    likes_list = [float(p["likes"]) for p in posts if p.get("likes") is not None]
    comm_list = [float(p["comments"]) for p in posts if p.get("comments") is not None]
    # Typical post: median beats mean when one post is an outlier; matches how we score.
    if len(likes_list) >= 2:
        avg_likes = float(statistics.median(likes_list))
    else:
        avg_likes = sum(likes_list) / len(likes_list) if likes_list else None
    if len(comm_list) >= 2:
        avg_comments = float(statistics.median(comm_list))
    else:
        avg_comments = sum(comm_list) / len(comm_list) if comm_list else None

    bits = [str(p.get("caption") or "") for p in posts[:3]]
    summary = "; ".join(s for s in bits if s)[:400]

    cand = CreatorCandidate(
        name=str(name)[:200],
        platform="TikTok",
        handle=handle if handle.startswith("@") else f"@{handle}",
        profile_url=profile_url,
        bio=str(bio)[:2000],
        follower_count=followers,
        recent_content_summary=summary or bio[:280],
        source_url=source_url,
        recent_posts=posts,
        avg_likes=avg_likes,
        avg_comments=avg_comments,
        engagement_rate=None,
    )
    apply_preferred_engagement_rate(cand)
    if not is_valid_enriched_profile(cand):
        return None
    return cand


def enrich_tiktok_creator(url: str, source_url: str) -> Optional[CreatorCandidate]:
    m = re.search(r"@([^/?#]+)", url)
    if not m:
        log_error(f"TikTok: could not parse handle from {url}")
        return None
    user = m.group(1)
    handle = f"@{user}"
    profile_url = profile_url_from_parts("TikTok", handle)

    settings = load_settings()
    actor = settings.get("apify", {}).get("tiktok_actor", "clockworks/tiktok-profile-scraper")
    # Clockworks actor expects `profiles` list of usernames (no @)
    run_input: Dict[str, Any] = {
        "profiles": [user],
        "resultsPerPage": 22,
    }

    items = _run_apify_actor(actor, run_input)
    return _tiktok_items_to_candidate(items, profile_url, handle, source_url)


def _instagram_items_to_candidate(
    items: List[Dict[str, Any]],
    profile_url: str,
    handle: str,
    source_url: str,
) -> Optional[CreatorCandidate]:
    if not items:
        return None
    root = items[0]
    # Common shapes: username, biography, followersCount, latestPosts
    name = root.get("fullName") or root.get("ownerFullName") or root.get("username") or handle
    bio = root.get("biography") or root.get("bio") or ""
    followers = None
    for k in ("followersCount", "followers", "edge_followed_by", "followedBy"):
        v = root.get(k)
        if isinstance(v, int):
            followers = v
            break
        if isinstance(v, dict) and "count" in v:
            followers = int(v["count"])
            break

    posts: List[Dict[str, Any]] = []
    raw_posts = (
        root.get("latestPosts")
        or root.get("posts")
        or root.get("edge_owner_to_timeline_media", {}).get("edges")
        or []
    )
    if isinstance(raw_posts, list):
        for it in raw_posts[:12]:
            if isinstance(it, dict) and "node" in it:
                it = it["node"]
            if not isinstance(it, dict):
                continue
            cap = it.get("caption") or it.get("text") or ""
            if isinstance(cap, dict):
                cap = cap.get("text") or ""
            posts.append({"caption": str(cap)[:500]})
            lk = it.get("likesCount") or it.get("edge_liked_by", {}).get("count")
            cm = it.get("commentsCount") or it.get("edge_media_to_comment", {}).get("count")
            if lk is not None:
                posts[-1]["likes"] = lk
            if cm is not None:
                posts[-1]["comments"] = cm
            views_raw = (
                it.get("videoViewCount")
                or it.get("viewCount")
                or it.get("playCount")
                or (it.get("video_play_count") if isinstance(it.get("video_play_count"), (int, float)) else None)
            )
            if views_raw is not None:
                try:
                    posts[-1]["views"] = int(views_raw)
                except (TypeError, ValueError):
                    pass

    likes_list = [float(p["likes"]) for p in posts if p.get("likes") is not None]
    comm_list = [float(p["comments"]) for p in posts if p.get("comments") is not None]
    if len(likes_list) >= 2:
        avg_likes = float(statistics.median(likes_list))
    else:
        avg_likes = sum(likes_list) / len(likes_list) if likes_list else None
    if len(comm_list) >= 2:
        avg_comments = float(statistics.median(comm_list))
    else:
        avg_comments = sum(comm_list) / len(comm_list) if comm_list else None

    bits = [str(p.get("caption") or "") for p in posts[:3]]
    summary = "; ".join(s for s in bits if s)[:400]

    cand = CreatorCandidate(
        name=str(name)[:200],
        platform="Instagram",
        handle=handle if handle.startswith("@") else f"@{handle}",
        profile_url=profile_url,
        bio=str(bio)[:2000],
        follower_count=followers,
        recent_content_summary=summary or bio[:280],
        source_url=source_url,
        recent_posts=posts,
        avg_likes=avg_likes,
        avg_comments=avg_comments,
        engagement_rate=None,
    )
    apply_preferred_engagement_rate(cand)
    if not is_valid_enriched_profile(cand):
        return None
    return cand


def enrich_instagram_creator(url: str, source_url: str) -> Optional[CreatorCandidate]:
    h = extract_handle_from_url(url, "Instagram")
    if not h:
        log_error(f"Instagram: could not parse handle from {url}")
        return None
    profile_url = profile_url_from_parts("Instagram", h)

    settings = load_settings()
    actor = settings.get("apify", {}).get("instagram_actor", "apify/instagram-scraper")
    username = h.lstrip("@")
    run_input: Dict[str, Any] = {
        "directUrls": [profile_url],
        "resultsType": "details",
        "resultsLimit": 1,
    }

    items = _run_apify_actor(actor, run_input)
    cand = _instagram_items_to_candidate(items, profile_url, h, source_url)
    if cand:
        return cand

    # Alternate input shape used by some builds
    run_input_alt = {"usernames": [username]}
    items = _run_apify_actor(actor, run_input_alt)
    return _instagram_items_to_candidate(items, profile_url, h, source_url)
