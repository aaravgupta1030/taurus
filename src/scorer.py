import json
import logging
import statistics
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from src.engagement_metrics import (
    aggregate_median_engagement_over_median_views,
    per_post_engagement_over_views_ratios,
    post_likes_comments,
)
from src.models import CreatorCandidate
from src.utils import get_env, load_prompt, load_settings, log_error, safe_json_loads

logger = logging.getLogger(__name__)

RUBRIC_CAPS: Dict[str, float] = {
    "relevance": 30.0,
    "audience_fit": 15.0,
    "creator_size": 10.0,
    "engagement_quality": 20.0,
    "content_quality": 10.0,
    "commercial_fit": 10.0,
    "brand_safety": 5.0,
}

COMMERCIAL_KEYWORDS = (
    "review",
    "product",
    "supplement",
    "affiliate",
    "discount code",
    "sponsored",
    "partnership",
    "amazon finds",
    "link in bio",
    "use code",
    "i tried",
    "promo",
    "ad",
)


def _keyword_relevance(
    niche_keywords: List[str],
    creator: CreatorCandidate,
) -> int:
    if not niche_keywords:
        return 0
    blob = " ".join(
        [
            creator.bio or "",
            creator.recent_content_summary or "",
            creator.name or "",
        ]
    ).lower()
    for p in creator.recent_posts:
        if isinstance(p, dict):
            blob += " " + str(p.get("caption") or p.get("title") or "").lower()
    score = 0
    for kw in niche_keywords:
        if len(kw) > 1 and kw.lower() in blob:
            score += 3
    return min(15, score)


def _creator_size_score(
    follower_count: Optional[int],
    constraints: Dict[str, Any],
) -> Tuple[int, bool]:
    """Returns (score, within_explicit_range)."""
    explicit_min = constraints.get("min_followers")
    explicit_max = constraints.get("max_followers")
    if follower_count is None:
        base = 4
    elif 10_000 <= follower_count <= 100_000:
        base = 10
    elif 5_000 <= follower_count < 10_000:
        base = 8
    elif 100_000 < follower_count <= 250_000:
        base = 8
    elif 1_000 <= follower_count < 5_000:
        base = 6
    elif 250_000 < follower_count <= 1_000_000:
        base = 5
    else:
        base = 3

    in_range = True
    if explicit_min is not None and follower_count is not None and follower_count < explicit_min:
        in_range = False
    if explicit_max is not None and follower_count is not None and follower_count > explicit_max:
        in_range = False
    if follower_count is None and (explicit_min is not None or explicit_max is not None):
        in_range = False

    if not in_range and (explicit_min is not None or explicit_max is not None):
        base = max(0, base - 4)
    return base, in_range


def _post_likes_comments(creator: CreatorCandidate) -> Tuple[List[float], List[float]]:
    """Per-post engagement — same nested-field rules as engagement_metrics.post_likes_comments."""
    likes: List[float] = []
    comments: List[float] = []
    for p in creator.recent_posts:
        if not isinstance(p, dict):
            continue
        lk, cm = post_likes_comments(p)
        if lk is not None:
            likes.append(float(lk))
            comments.append(float(cm))
    return likes, comments


def _median_or_none(vals: List[float]) -> Optional[float]:
    if not vals:
        return None
    if len(vals) == 1:
        return float(vals[0])
    return float(statistics.median(vals))


def _viral_skew_multiplier(likes: List[float]) -> Tuple[float, float]:
    """
    Penalize one viral outlier vs typical performance (median).
    Returns (multiplier in (0,1], max/median ratio for transparency).
    """
    if len(likes) < 2:
        return 1.0, 1.0
    med = statistics.median(likes)
    mx = max(likes)
    if med <= 0:
        return 1.0, float(mx)
    ratio = mx / med
    if ratio <= 3.5:
        mult = 1.0
    elif ratio <= 6:
        mult = 0.82
    elif ratio <= 10:
        mult = 0.62
    elif ratio <= 20:
        mult = 0.42
    else:
        mult = max(0.22, 0.42 - 0.015 * (ratio - 20))
    # Many recent posts under ~1k engagement while one post is huge → likely one-hit pattern
    if len(likes) >= 3:
        below_floor = sum(1 for x in likes if x < 1000)
        if mx >= 5000 and below_floor >= max(2, (len(likes) + 1) // 2):
            mult *= 0.55
    return mult, ratio


def _tier_score_from_engagement_rate(er: float) -> int:
    """Tiers for (likes+comments) / follower_count (legacy short-form style)."""
    if er >= 0.08:
        return 20
    if er >= 0.05:
        return 17
    if er >= 0.03:
        return 14
    if er >= 0.015:
        return 10
    if er >= 0.005:
        return 6
    return 3


def _tier_score_from_engagement_rate_view_based(er: float) -> int:
    """Tiers for median (likes+comments) / views per post (typical short-form video range)."""
    if er >= 0.12:
        return 20
    if er >= 0.08:
        return 17
    if er >= 0.05:
        return 14
    if er >= 0.03:
        return 10
    if er >= 0.015:
        return 6
    return 3


def _engagement_score_block(
    creator: CreatorCandidate,
    constraints: Dict[str, Any],
) -> Tuple[int, Optional[float], Dict[str, Any]]:
    """
    Use median per-post engagement when enough samples exist (dampens one viral video).
    Apply a consistency multiplier when max/median likes is very high.
    """
    fc = creator.follower_count
    likes, comments = _post_likes_comments(creator)
    extra: Dict[str, Any] = {}

    md_l = _median_or_none(likes) if len(likes) >= 2 else None
    md_c = _median_or_none(comments) if len(comments) >= 2 else None
    if md_l is None and len(likes) == 1:
        md_l = float(likes[0])
    if md_c is None and len(comments) == 1:
        md_c = float(comments[0])

    er: Optional[float] = None
    agg_er, agg_n = aggregate_median_engagement_over_median_views(creator.recent_posts)
    if agg_er is not None and agg_n >= 2:
        er = float(agg_er)
        extra["engagement_rate_basis"] = "median_engagement_over_median_views"
    else:
        view_ratios = per_post_engagement_over_views_ratios(creator.recent_posts)
        if len(view_ratios) >= 2:
            er = float(statistics.median(view_ratios))
            extra["engagement_rate_basis"] = "median_per_post_over_views"
        elif len(view_ratios) == 1:
            er = float(view_ratios[0])
            extra["engagement_rate_basis"] = "single_post_over_views"

    if er is None and fc and fc > 0 and md_l is not None:
        er = (md_l + (md_c or 0)) / float(fc)
        extra["engagement_rate_basis"] = "median_likes_comments_over_followers"
    elif er is None and creator.engagement_rate is not None:
        er = creator.engagement_rate
        extra["engagement_rate_basis"] = "precomputed"
    elif er is None and fc and fc > 0 and creator.avg_likes is not None:
        er = (creator.avg_likes + (creator.avg_comments or 0)) / float(fc)
        extra["engagement_rate_basis"] = "mean_avg_fields_over_followers"

    mult, skew = _viral_skew_multiplier(likes)
    extra["viral_skew_max_over_median"] = round(skew, 2)
    extra["engagement_consistency_multiplier"] = round(mult, 3)

    if er is None:
        extra["engagement_rate_effective"] = None
        return 10, None, extra

    extra["engagement_rate_effective"] = round(er, 6)
    basis = str(extra.get("engagement_rate_basis") or "")
    view_based = basis in (
        "median_engagement_over_median_views",
        "median_per_post_over_views",
        "single_post_over_views",
    ) or "over_views" in basis
    if view_based:
        base = _tier_score_from_engagement_rate_view_based(er)
    else:
        base = _tier_score_from_engagement_rate(er)
    adjusted = max(2, int(round(base * mult)))
    extra["engagement_quality_raw_tier"] = base
    creator.engagement_rate = er
    return adjusted, er, extra


def _commercial_keyword_hint(creator: CreatorCandidate) -> str:
    blob = (creator.bio or "") + " " + (creator.recent_content_summary or "")
    for p in creator.recent_posts:
        if isinstance(p, dict):
            blob += " " + str(p.get("caption") or "")
    b = blob.lower()
    hits = [k for k in COMMERCIAL_KEYWORDS if k in b]
    return ", ".join(hits[:12]) if hits else "none"


def _llm_score_bundle(
    user_query: str,
    niche_keywords: List[str],
    creator: CreatorCandidate,
) -> Optional[Dict[str, Any]]:
    key = get_env("OPENAI_API_KEY")
    if not key:
        return None
    client = OpenAI(api_key=key)
    sys_prompt = (
        "You are a strict marketing evaluator. Reply ONLY with compact JSON, no markdown.\n"
        "Keys and allowed ranges:\n"
        "llm_relevance_subscore: integer 0-15 (niche alignment of posts relative to the query)\n"
        "audience_fit: integer 0-15\n"
        "content_quality: integer 0-10\n"
        "commercial_fit: integer 0-10\n"
        "brand_safety: integer 0-5\n"
        "Use the rubric from the user message."
    )
    lk, _cm = _post_likes_comments(creator)
    eng_hint = ""
    if len(lk) >= 2:
        med = statistics.median(lk)
        eng_hint = (
            f"Recent post like counts (sample): {lk[:10]!s}; "
            f"median={med:.0f}, max={max(lk):.0f}. "
            "Penalize one viral outlier vs steady performance when scoring content/audience fit."
        )

    user_blob = json.dumps(
        {
            "user_query": user_query,
            "niche_keywords": niche_keywords,
            "name": creator.name,
            "platform": creator.platform,
            "handle": creator.handle,
            "bio": creator.bio[:1500],
            "recent_content_summary": creator.recent_content_summary[:800],
            "recent_post_text": [
                str(
                    (p.get("caption") or p.get("title") or "")[:300]
                    if isinstance(p, dict)
                    else ""
                )
                for p in creator.recent_posts[:6]
            ],
            "commercial_keyword_hits": _commercial_keyword_hint(creator),
            "post_engagement_hint": eng_hint,
        },
        ensure_ascii=False,
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.2,
            messages=[
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": user_blob,
                },
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        return safe_json_loads(text)
    except Exception as e:  # noqa: BLE001
        log_error(f"LLM scoring bundle failed: {e}")
        return None


def _reason_text(
    user_query: str,
    creator: CreatorCandidate,
    breakdown: Dict[str, Any],
) -> str:
    key = get_env("OPENAI_API_KEY")
    template = load_prompt("scoring_reason.txt")
    if not key:
        return (
            f"Fit based on query '{user_query[:80]}' with total score {breakdown.get('total')}."
        )
    client = OpenAI(api_key=key)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.35,
            messages=[
                {"role": "system", "content": template},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "user_query": user_query,
                            "creator": {
                                "name": creator.name,
                                "platform": creator.platform,
                                "handle": creator.handle,
                                "follower_count": creator.follower_count,
                                "bio": creator.bio[:600],
                                "recent_content_summary": creator.recent_content_summary[:500],
                            },
                            "score_breakdown": breakdown,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        out = (resp.choices[0].message.content or "").strip()
        if out:
            return out[:1200]
    except Exception as e:  # noqa: BLE001
        log_error(f"reason generation failed: {e}")
    return (
        f"Score {breakdown.get('total')}: strong {breakdown.get('relevance')} relevance, "
        f"{breakdown.get('audience_fit')} audience fit; "
        f"size/engagement {breakdown.get('creator_size')}/{breakdown.get('engagement_quality')}."
    )


def _weighted_rank_score_0_100(breakdown: Dict[str, Any], weights: Dict[str, Any]) -> float:
    """Normalize each rubric bucket to 0–1, then weighted average → 0–100."""
    ws = 0.0
    tw = 0.0
    for key, cap in RUBRIC_CAPS.items():
        raw = breakdown.get(key)
        if not isinstance(raw, (int, float)):
            continue
        v = max(0.0, min(float(cap), float(raw)))
        w = float(weights.get(key, 1.0))
        ws += w * (v / cap)
        tw += w
    if tw <= 0:
        return float(breakdown.get("total", 0) or 0)
    return (ws / tw) * 100.0


def _apply_rerank_and_calibration(
    breakdown: Dict[str, Any],
    *,
    legacy_total: int,
) -> int:
    """
    Replace display total with weighted rank + global calibration.
    Preserves legacy sum in breakdown for audit.
    """
    settings = load_settings()
    rer = settings.get("rerank") or {}
    cal = settings.get("calibration") or {}

    if rer.get("enabled", True):
        weights = rer.get("weights") or {}
        weighted = _weighted_rank_score_0_100(breakdown, weights)
    else:
        weighted = float(legacy_total)

    breakdown["legacy_rubric_sum"] = legacy_total
    breakdown["weighted_rank_pre_calibration"] = round(weighted, 4)

    if cal.get("enabled", True):
        slope = float(cal.get("slope", 1.0))
        intercept = float(cal.get("intercept", 0.0))
        final = int(round(min(100.0, max(0.0, weighted * slope + intercept))))
    else:
        final = int(round(min(100.0, max(0.0, weighted))))

    breakdown["total"] = final
    return final


def score_creator(
    user_query: str,
    creator: CreatorCandidate,
    constraints: Dict[str, Any],
) -> CreatorCandidate:
    """Compute weighted 100-point score and explanation (BUILD §13–15)."""
    niche_keywords = constraints.get("niche_keywords") or []

    kw_rel = _keyword_relevance(niche_keywords, creator)
    bundle = _llm_score_bundle(user_query, niche_keywords, creator)

    if bundle:
        llm_rel = int(max(0, min(15, bundle.get("llm_relevance_subscore", 0))))
        audience_fit = int(max(0, min(15, bundle.get("audience_fit", 0))))
        content_quality = int(max(0, min(10, bundle.get("content_quality", 0))))
        commercial_fit = int(max(0, min(10, bundle.get("commercial_fit", 0))))
        brand_safety = int(max(0, min(5, bundle.get("brand_safety", 0))))
    else:
        llm_rel = 8
        audience_fit = 8
        content_quality = 6
        commercial_fit = 5
        brand_safety = 4

    relevance = min(30, kw_rel + llm_rel)

    size_score, _ = _creator_size_score(creator.follower_count, constraints)

    eng_block, er, eng_extra = _engagement_score_block(creator, constraints)
    if er is not None:
        creator.engagement_rate = er

    if creator.follower_count is None and bundle is None:
        eng_block = min(eng_block, 12)

    fit_score = int(
        round(
            relevance
            + audience_fit
            + size_score
            + eng_block
            + content_quality
            + commercial_fit
            + brand_safety
        )
    )
    fit_score = max(0, min(100, fit_score))

    breakdown: Dict[str, Any] = {
        "relevance": relevance,
        "keyword_relevance": kw_rel,
        "llm_relevance": llm_rel,
        "audience_fit": audience_fit,
        "creator_size": size_score,
        "engagement_quality": eng_block,
        "content_quality": content_quality,
        "commercial_fit": commercial_fit,
        "brand_safety": brand_safety,
        "total": fit_score,
        **eng_extra,
    }

    display_score = _apply_rerank_and_calibration(breakdown, legacy_total=fit_score)
    creator.fit_score = display_score
    creator.score_breakdown = breakdown
    creator.reason = _reason_text(user_query, creator, breakdown)
    return creator
