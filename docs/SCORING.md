# Scoring (100 points)

Weights match `BUILD.md`:

| Category          | Points |
|-------------------|--------|
| Relevance         | 30     |
| Audience fit      | 15     |
| Creator size      | 10     |
| Engagement        | 20     |
| Content quality   | 10     |
| Commercial fit    | 10     |
| Brand safety      | 5      |

## Relevance (30)

- Up to 15 points from keyword overlap between niche keywords (parsed from the user query) and the creator bio, summary, and recent post text.
- Up to 15 points from an LLM judgment (`llm_relevance_subscore`) capped so the sum does not exceed 30.

## Audience fit (15)

- LLM-only score against the brand/niche query.

## Creator size (10)

- Deterministic buckets by follower ranges.
- If the user supplied explicit min/max followers and the creator falls outside that band, subtract 4 points from the size component (floored at 0).

## Engagement quality (20)

- When **per-post** like counts exist for at least two recent posts, engagement rate uses **medians** (not means): `(median_likes + median_comments) / followers`. That way one viral upload does not dominate the rate the way a mean would.
- That rate maps to the same 0–20 tier table as before (`BUILD.md` §13.4).
- **Consistency penalty:** if `max(likes) / median(likes)` is high (one outlier), the tier score is multiplied down (e.g. very spiky → roughly 22–45% of the tier score). If at least half of sampled posts are under **1k** likes while the max is **5k+**, an extra multiplier applies (typical “one hit, rest flat” pattern).
- `score_breakdown` includes `viral_skew_max_over_median`, `engagement_consistency_multiplier`, `engagement_quality_raw_tier`, and `engagement_rate_basis` for transparency.
- When post-level likes are missing, the scorer falls back to precomputed `engagement_rate` / `avg_likes` fields from enrichment.

## Content quality (10)

- LLM judgment of clarity, usefulness, and authenticity.

## Commercial fit (10)

- LLM judgment informed by commercial keywords detected in bios/captions when available.

## Brand safety (5)

- Conservative LLM screen for obvious outreach risks.

## Rubric sum (legacy)

The seven buckets are still computed as in `BUILD.md`; their sum is stored as **`legacy_rubric_sum`** in `score_breakdown` for audit.

## Weighted rank + global calibration (display score)

The **`fit_score`** shown in the UI and CSV is **not** the raw rubric sum anymore:

1. Each bucket is normalized by its max points (e.g. relevance ÷ 30).
2. A **weighted average** of those normalized scores yields a 0–100 **weighted rank** (`weighted_rank_pre_calibration`). Weights live in `config/settings.yaml` under `rerank.weights` (engagement is up-weighted for recall goals).
3. A **global linear map** is applied: `fit_score = clamp(0, 100, weighted × slope + intercept)` using `calibration` in the same file. Same parameters every run so scores stay comparable across queries.

## Discovery recall

`config/settings.yaml` → `recall` controls a larger SerpAPI pool, optional **expansion query** pass (`generate_recall_expansion_queries`), and a higher **cap on enriched profiles** before dedupe/scoring—see `src/pipeline.py`.
