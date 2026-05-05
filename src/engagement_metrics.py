"""Engagement rate helpers: prefer (likes+comments)/views per post when views exist."""
from __future__ import annotations

import statistics
from typing import Any, Dict, List, Optional, Tuple

from src.models import CreatorCandidate
from src.utils import load_settings


def _as_int(v: Any) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        if v != v:  # NaN
            return None
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def count_posts_with_views(posts: List[Any]) -> int:
    """How many recent posts have a positive view / play count (for strict scrape validation)."""
    n = 0
    for p in posts:
        if not isinstance(p, dict):
            continue
        v = post_views(p)
        if v is not None and v > 0:
            n += 1
    return n


def post_views(post: Dict[str, Any]) -> Optional[int]:
    """Best-effort views/play count from Apify / YouTube post dicts."""
    if not isinstance(post, dict):
        return None
    for key in ("views", "playCount", "viewCount", "videoViewCount", "videoPlayCount"):
        n = _as_int(post.get(key))
        if n is not None and n > 0:
            return n
    vm = post.get("videoMeta")
    if isinstance(vm, dict):
        n = _as_int(vm.get("playCount") or vm.get("viewCount"))
        if n is not None and n > 0:
            return n
    st = post.get("stats")
    if isinstance(st, dict):
        n = _as_int(st.get("playCount") or st.get("viewCount"))
        if n is not None and n > 0:
            return n
    return None


def _float_metric(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x != x:  # NaN
        return None
    return x if x >= 0 else None


def _likes_from_layer(d: Dict[str, Any]) -> Optional[float]:
    for key in ("likes", "diggCount", "diggedCount", "likeCount", "digg"):
        fv = _float_metric(d.get(key))
        if fv is not None:
            return fv
    return None


def _comments_from_layer(d: Dict[str, Any]) -> Optional[float]:
    for key in ("comments", "commentCount", "replyCommentTotal", "comment"):
        fv = _float_metric(d.get(key))
        if fv is not None:
            return fv
    return None


def post_likes_comments(post: Dict[str, Any]) -> Tuple[Optional[float], float]:
    """
    Likes + comments for one post. Merges TikTok/Clockworks-style nesting (stats, video,
    videoMeta, …) so we never treat nested diggCount as missing — that bug caused
    near-zero averages and fake-low engagement rates.
    """
    if not isinstance(post, dict):
        return None, 0.0
    layers: List[Dict[str, Any]] = [post]
    for nest in ("stats", "itemStats", "videoStats", "video", "videoMeta"):
        blk = post.get(nest)
        if isinstance(blk, dict):
            layers.append(blk)
    best_l: Optional[float] = None
    best_c: Optional[float] = None
    for d in layers:
        lv = _likes_from_layer(d)
        if lv is not None and (best_l is None or lv > best_l):
            best_l = lv
        cv = _comments_from_layer(d)
        if cv is not None and (best_c is None or cv > best_c):
            best_c = cv
    comments_f = float(best_c) if best_c is not None else 0.0
    return best_l, comments_f


def per_post_engagement_over_views_ratios(posts: List[Dict[str, Any]]) -> List[float]:
    out: List[float] = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        v = post_views(p)
        if v is None or v <= 0:
            continue
        likes_f, comments_f = post_likes_comments(p)
        if likes_f is None:
            continue
        out.append((likes_f + comments_f) / float(v))
    return out


def median_engagement_over_views(posts: List[Dict[str, Any]]) -> Tuple[Optional[float], int]:
    ratios = per_post_engagement_over_views_ratios(posts)
    if not ratios:
        return None, 0
    if len(ratios) == 1:
        return float(ratios[0]), 1
    return float(statistics.median(ratios)), len(ratios)


def aggregate_median_engagement_over_median_views(
    posts: List[Dict[str, Any]],
) -> Tuple[Optional[float], int]:
    """
    median(likes + comments per post) / median(views per post).

    More stable than median(per-post ratio): one mega-view post no longer drags
    the headline rate down as hard, and it matches how marketers read dashboards.
    """
    eng: List[float] = []
    views: List[float] = []
    for p in posts:
        if not isinstance(p, dict):
            continue
        v = post_views(p)
        lk, cm = post_likes_comments(p)
        if v is None or v <= 0 or lk is None:
            continue
        eng.append(float(lk) + float(cm))
        views.append(float(v))
    if len(eng) < 2:
        return None, 0
    med_e = float(statistics.median(eng))
    med_v = float(statistics.median(views))
    if med_v <= 0:
        return None, 0
    return med_e / med_v, len(eng)


def follower_based_rate_from_averages(
    follower_count: Optional[int],
    avg_likes: Optional[float],
    avg_comments: Optional[float],
) -> Optional[float]:
    if not follower_count or follower_count <= 0 or avg_likes is None:
        return None
    return (float(avg_likes) + float(avg_comments or 0)) / float(follower_count)


def apply_preferred_engagement_rate(creator: CreatorCandidate) -> str:
    """
    Set creator.engagement_rate after enrichment (before scoring overwrites it).
    Prefer median (likes+comments)/views across posts with views; else follower averages.
    Returns a short basis label (scoring adds the canonical breakdown.engagement_rate_basis).
    """
    agg, n_agg = aggregate_median_engagement_over_median_views(creator.recent_posts)
    if agg is not None and n_agg >= 2:
        creator.engagement_rate = float(agg)
        return "median_engagement_over_median_views"

    md, n = median_engagement_over_views(creator.recent_posts)
    if md is not None and n >= 1:
        creator.engagement_rate = md
        return "median_per_post_over_views" if n >= 2 else "single_post_over_views"
    fb = follower_based_rate_from_averages(
        creator.follower_count, creator.avg_likes, creator.avg_comments
    )
    if fb is not None:
        creator.engagement_rate = fb
        return "avg_over_followers"
    creator.engagement_rate = None
    return "none"


def passes_engagement_quality_gate(
    c: CreatorCandidate,
    settings: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Drop creators whose typical post engagement is implausibly weak vs followers,
    or (for short-form video platforms) whose median engagement vs views is too low.

    Uses the same post signals as scoring so bad scrapes and ghost metrics do not surface.
    """
    cfg = ((settings or load_settings()).get("engagement_quality_gate")) or {}
    if cfg.get("enabled") is False:
        return True

    min_follower_tt_yt = float(cfg.get("min_typical_post_engagement_over_followers", 0.0012))
    min_follower_ig = float(
        cfg.get("min_typical_post_engagement_over_followers_instagram", 0.00135)
    )
    posts = c.recent_posts or []
    fc = int(c.follower_count) if c.follower_count is not None and c.follower_count > 0 else 0

    follower_ok = True
    if c.platform in ("TikTok", "YouTube"):
        mf = min_follower_tt_yt
        if fc <= 0 or c.avg_likes is None:
            follower_ok = False
        else:
            typical = float(c.avg_likes) + float(c.avg_comments or 0)
            follower_ok = typical / float(fc) >= mf
    else:
        mf = min_follower_ig
        if fc > 0 and c.avg_likes is not None:
            typical = float(c.avg_likes) + float(c.avg_comments or 0)
            follower_ok = typical / float(fc) >= mf

    view_ok = True
    platforms = cfg.get("apply_view_gate_to_platforms") or ["TikTok", "YouTube"]
    if c.platform in platforms and count_posts_with_views(posts) >= 2:
        byp = cfg.get("min_view_based_engagement_rate_by_platform") or {}
        default_v = float(cfg.get("min_view_based_engagement_rate", 0.03))
        thresh = float(byp.get(c.platform, default_v))
        agg, n_agg = aggregate_median_engagement_over_median_views(posts)
        if agg is not None and n_agg >= 2:
            view_ok = float(agg) >= thresh
        else:
            md, n_md = median_engagement_over_views(posts)
            if md is not None and n_md >= 2:
                view_ok = float(md) >= thresh

    return follower_ok and view_ok


def passes_user_prompt_metric_floors(
    c: CreatorCandidate,
    constraints: Dict[str, Any],
) -> bool:
    """
    Hard filters from the user query (regex + optional LLM).

    For any bound the user specified, the creator must have that metric present (when a min or
    max applies) and satisfy all intervals: followers, avg likes, avg comments, engagement rate.
    """
    min_f = constraints.get("min_followers")
    max_f = constraints.get("max_followers")
    if min_f is not None or max_f is not None:
        fc = c.follower_count
        if fc is None:
            return False
        try:
            fc_i = int(fc)
        except (TypeError, ValueError):
            return False
        if min_f is not None and fc_i < int(min_f):
            return False
        if max_f is not None and fc_i > int(max_f):
            return False

    min_l = constraints.get("min_avg_likes")
    max_l = constraints.get("max_avg_likes")
    if min_l is not None or max_l is not None:
        if c.avg_likes is None:
            return False
        al = float(c.avg_likes)
        if min_l is not None and al < float(min_l):
            return False
        if max_l is not None and al > float(max_l):
            return False

    min_c = constraints.get("min_avg_comments")
    max_c = constraints.get("max_avg_comments")
    if min_c is not None or max_c is not None:
        ac = c.avg_comments
        if ac is None:
            return False
        ac_f = float(ac)
        if min_c is not None and ac_f < float(min_c):
            return False
        if max_c is not None and ac_f > float(max_c):
            return False

    min_er = constraints.get("min_engagement_rate")
    max_er = constraints.get("max_engagement_rate")
    if min_er is not None or max_er is not None:
        if c.engagement_rate is None:
            return False
        er = float(c.engagement_rate)
        if min_er is not None and er < float(min_er):
            return False
        if max_er is not None and er > float(max_er):
            return False
    return True


def passes_headline_engagement_floor(
    c: CreatorCandidate,
    settings: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Final filter on the same engagement_rate the UI shows as a percent (fraction 0–1).

    Typical use: require at least 10% (= 0.10) for TikTok/YouTube where the headline value is
    usually (likes + comments) / views. Instagram is often follower-based; omit from
    config `platforms` unless you accept a separate scale.
    """
    cfg = ((settings or load_settings()).get("headline_engagement_floor")) or {}
    if cfg.get("enabled") is False:
        return True
    platforms = cfg.get("platforms") or ["TikTok", "YouTube"]
    if c.platform not in platforms:
        return True
    er = c.engagement_rate
    if er is None:
        return False
    return float(er) >= float(cfg.get("min_fraction", 0.10))


def is_valid_enriched_profile(c: CreatorCandidate) -> bool:
    """
    Drop ghost / failed scrapes.

    TikTok and YouTube: require at least two recent posts with a positive view/play
    count so we only keep profiles where reach is confirmed in the payload.

    Instagram: lighter rule — Reels often have views, but photo carousels may omit
    them, so we accept strong signals from followers, captions, likes, or comments.
    """
    posts = c.recent_posts or []
    bio = (c.bio or "").strip()
    fc = c.follower_count

    has_followers = fc is not None and fc > 0
    n_posts = len(posts)
    posts_with_views = count_posts_with_views(posts)

    has_post_text = any(
        isinstance(p, dict)
        and (str(p.get("caption") or p.get("title") or p.get("description") or "").strip())
        for p in posts
    )
    has_post_metrics = any(
        isinstance(p, dict)
        and (
            p.get("likes") is not None
            or p.get("diggCount") is not None
            or p.get("comments") is not None
            or post_views(p) is not None
        )
        for p in posts
    )

    if c.platform in ("TikTok", "YouTube"):
        if posts_with_views < 2:
            return False
        if not passes_engagement_quality_gate(c):
            return False
        return True

    if c.platform == "Instagram":
        if posts_with_views >= 2:
            return passes_engagement_quality_gate(c)
        if has_followers:
            return passes_engagement_quality_gate(c)
        if n_posts > 0 and (has_post_metrics or has_post_text):
            return passes_engagement_quality_gate(c)
        return (len(bio) >= 40 and (has_post_metrics or n_posts > 0)) and passes_engagement_quality_gate(
            c
        )

    if has_followers:
        return passes_engagement_quality_gate(c)
    if n_posts > 0 and (has_post_metrics or has_post_text):
        return passes_engagement_quality_gate(c)
    return (len(bio) >= 40 and (has_post_metrics or n_posts > 0)) and passes_engagement_quality_gate(c)
