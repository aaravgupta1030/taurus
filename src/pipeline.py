"""Shared discovery + scoring pipeline for CLI and web API."""
import concurrent.futures
import copy
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Set, Tuple

from src.apify_client import enrich_instagram_creator, enrich_tiktok_creator
from src.engagement_metrics import (
    is_valid_enriched_profile,
    passes_engagement_quality_gate,
    passes_headline_engagement_floor,
    passes_user_prompt_metric_floors,
)
from src.deduper import dedupe_creators
from src.extractor import summarize_recent_content
from src.models import CreatorCandidate
from src.platform_router import (
    detect_platform,
    extract_handle_from_url,
    is_likely_non_creator_page,
)
from src.query_planner import (
    generate_platform_balance_queries,
    generate_recall_expansion_queries,
    generate_search_queries,
)
from src.scorer import score_creator
from src.search_serpapi import search_google
from src.youtube_client import enrich_youtube_creator
from src.utils import load_env, load_settings, outputs_dir, parse_query_constraints


def _enrich_one(url: str, source_url: str) -> Optional[CreatorCandidate]:
    plat = detect_platform(url)
    if plat == "YouTube":
        return enrich_youtube_creator(url, source_url)
    if plat == "TikTok":
        return enrich_tiktok_creator(url, source_url)
    if plat == "Instagram":
        return enrich_instagram_creator(url, source_url)
    return None


def _enrich_parallel(pairs: List[Tuple[str, str]], workers: int) -> List[CreatorCandidate]:
    """Enrich many profile links at once (Apify / YouTube HTTP)."""
    if not pairs:
        return []
    w = max(1, min(int(workers), len(pairs)))
    out: List[CreatorCandidate] = []
    with ThreadPoolExecutor(max_workers=w) as pool:
        futures = [pool.submit(_enrich_one, link, src) for link, src in pairs]
        for fut in concurrent.futures.as_completed(futures):
            try:
                c = fut.result()
            except Exception as e:  # noqa: BLE001
                logging.warning("Enrich failed: %s", e)
                continue
            if c and is_valid_enriched_profile(c):
                out.append(c)
    return out


def _summarize_and_score_one(args: Tuple[CreatorCandidate, str, dict]) -> CreatorCandidate:
    c, user_query, constraints = args
    c.recent_content_summary = summarize_recent_content(c, user_query)
    score_creator(user_query, c, constraints)
    return c


def _should_skip_link(url: str, constraints: dict) -> bool:
    plat = detect_platform(url)
    if not plat:
        return True
    if is_likely_non_creator_page(url, plat):
        return True
    wanted = constraints.get("platforms") or []
    if wanted and plat not in wanted:
        return True
    if plat == "YouTube" and ("youtube.com/playlist" in url.lower() or "/playlist?list=" in url.lower()):
        return True
    if plat in ("TikTok", "Instagram") and not extract_handle_from_url(url, plat):
        return True
    return False


def run_pipeline(user_query: str) -> List[CreatorCandidate]:
    load_env()
    (outputs_dir() / "errors.log").write_text("", encoding="utf-8")
    settings = load_settings()
    recall = settings.get("recall") or {}
    min_target = int(settings.get("min_creators_target", 10))
    collect_cap = int(recall.get("max_enriched_candidates", 24))
    raw_mult = int(recall.get("min_target_raw_multiplier", 6))
    expand_raw = bool(recall.get("expand_collect_for_min_target", True))
    enrich_budget = (
        max(collect_cap, min_target * raw_mult) if expand_raw else collect_cap
    )
    per_phase_q = int(
        recall.get("serpapi_max_queries_per_phase", settings.get("serpapi_max_queries", 16))
    )
    per_q = int(recall.get("serpapi_results_per_query", settings.get("serpapi_results_per_query", 12)))
    max_links = int(recall.get("max_links", 240))
    exp_cfg = recall.get("expansion") or {}
    expansion_enabled = bool(exp_cfg.get("enabled", True))
    expansion_max_q = int(exp_cfg.get("max_queries", 8))
    enrich_workers = max(1, int(recall.get("enrich_workers", 4)))
    score_workers = max(1, int(recall.get("score_workers", 4)))

    constraints = parse_query_constraints(user_query)
    seen_urls: Set[str] = set()
    candidates: List[CreatorCandidate] = []
    active_constraints = constraints

    def collect(queries: List[str], c_local: dict, max_queries_phase: int) -> None:
        q_used = 0
        for q in queries:
            if q_used >= max_queries_phase:
                break
            if len(candidates) >= enrich_budget:
                return
            q_used += 1
            results = search_google(q, max_results=per_q)
            batch: List[Tuple[str, str]] = []
            for item in results:
                if len(seen_urls) >= max_links:
                    break
                link = (item.get("link") or "").strip()
                if not link or link in seen_urls:
                    continue
                if _should_skip_link(link, c_local):
                    continue
                seen_urls.add(link)
                src = item.get("source_query") or link
                batch.append((link, src))
            if not batch or len(candidates) >= enrich_budget:
                continue
            for enriched in _enrich_parallel(batch, enrich_workers):
                if len(candidates) >= enrich_budget:
                    return
                candidates.append(enriched)
                logging.info("Enriched %s (%s)", enriched.handle, enriched.platform)

    balance = generate_platform_balance_queries(user_query, active_constraints)
    collect(balance, active_constraints, len(balance))
    queries = generate_search_queries(user_query, active_constraints, fallback=False)
    collect(queries, active_constraints, per_phase_q)

    if len(candidates) < min_target:
        relaxed = copy.deepcopy(constraints)
        relaxed["platforms"] = []
        relaxed["min_followers"] = None
        relaxed["max_followers"] = None
        active_constraints = relaxed
        q2 = generate_search_queries(user_query, relaxed, fallback=True)
        collect(q2, relaxed, per_phase_q)

    if expansion_enabled and len(candidates) < enrich_budget:
        q_exp = generate_recall_expansion_queries(user_query, active_constraints)
        collect(q_exp, active_constraints, expansion_max_q)

    unique = dedupe_creators(candidates)
    work = [(c, user_query, constraints) for c in unique]
    with ThreadPoolExecutor(max_workers=min(score_workers, max(1, len(work)))) as pool:
        scored = list(pool.map(_summarize_and_score_one, work)) if work else []

    scored.sort(key=lambda x: (x.fit_score or 0), reverse=True)
    gated = [c for c in scored if passes_engagement_quality_gate(c, settings)]
    if len(gated) < len(scored):
        logging.info(
            "Engagement quality gate kept %d / %d creators after scoring",
            len(gated),
            len(scored),
        )
    before_floor = len(gated)
    gated = [c for c in gated if passes_headline_engagement_floor(c, settings)]
    if len(gated) < before_floor:
        logging.info(
            "Headline engagement floor (>= %.0f%% on %s) kept %d / %d creators",
            float((settings.get("headline_engagement_floor") or {}).get("min_fraction", 0.10)) * 100,
            ", ".join((settings.get("headline_engagement_floor") or {}).get("platforms") or ["TikTok", "YouTube"]),
            len(gated),
            before_floor,
        )
    before_prompt = len(gated)
    gated = [c for c in gated if passes_user_prompt_metric_floors(c, constraints)]
    if len(gated) < before_prompt:
        logging.info(
            "User prompt metric floors (avg likes/comments / engagement) kept %d / %d creators",
            len(gated),
            before_prompt,
        )
    if not gated:
        logging.warning(
            "All creators were filtered (engagement_quality_gate, headline_engagement_floor, "
            "and/or user prompt metric floors); relax config/settings.yaml or broaden the query"
        )
    return gated
