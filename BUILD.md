# Creator Sourcing Agent — Cursor Implementation Spec

## 0. What Cursor must build

Build a Python project that takes a brand/niche query such as:

```bash
python main.py "dog wellness creators"
```

and returns at least 10 real TikTok, Instagram, or YouTube creators in JSON/CSV format.

The system must:

1. Search public internet sources for creator profiles and creator-like content.
2. Collect real profile metadata.
3. Enrich each creator with bio, followers, platform, URL, and recent content summary.
4. Score each creator using a transparent weighted scoring system.
5. Return the top ranked creators with an explainable reason.
6. Save outputs to `outputs/creators.json` and `outputs/creators.csv`.

Do not use mocked or hardcoded creators. Every creator must come from a real search/API/scraper result.

---

## 1. Required API keys

The user must provide these in a `.env` file:

```env
OPENAI_API_KEY=...
SERPAPI_KEY=...
YOUTUBE_API_KEY=...
APIFY_API_TOKEN=...
```

Use:

- OpenAI API for query generation, extraction cleanup, content summarization, and reason generation.
- SerpAPI for Google search discovery.
- YouTube Data API for YouTube creator enrichment.
- Apify actors for TikTok and Instagram scraping/enrichment.

---

## 2. Exact project structure to create

Create this project:

```text
taurus/                    # repo root (your folder name may differ)
├── main.py
├── README.md
├── requirements.txt
├── .env.example
├── config/
│   └── settings.yaml
├── src/
│   ├── __init__.py
│   ├── models.py
│   ├── query_planner.py
│   ├── search_serpapi.py
│   ├── platform_router.py
│   ├── youtube_client.py
│   ├── apify_client.py
│   ├── extractor.py
│   ├── scorer.py
│   ├── deduper.py
│   ├── output_writer.py
│   └── utils.py
├── prompts/
│   ├── query_generation.txt
│   ├── creator_extraction.txt
│   ├── content_summary.txt
│   └── scoring_reason.txt
├── outputs/
│   ├── creators.json
│   └── creators.csv
└── docs/
    ├── DATA_SOURCES.md
    ├── SCORING.md
    ├── TRADEOFFS.md
    └── SCALING.md
```

---

## 3. Overall data flow

Implement this exact pipeline:

```text
User query
  ↓
Parse query constraints
  ↓
Generate platform-specific search queries
  ↓
Search Google via SerpAPI
  ↓
Collect candidate URLs
  ↓
Route URLs by platform
  ↓
Enrich creator profiles using YouTube API or Apify
  ↓
Deduplicate creators
  ↓
Summarize recent content
  ↓
Score each creator with transparent weighted formula
  ↓
Sort by fit_score descending
  ↓
Save JSON and CSV outputs
```

The system should keep collecting candidates until it has at least 10 valid scored creators or until it has exhausted the search budget.

---

## 4. Input query handling

The input can be plain text:

```bash
python main.py "pet supplements Instagram creators under 50k followers"
```

Cursor should implement a parser that extracts soft constraints from the text:

```python
{
  "raw_query": "pet supplements Instagram creators under 50k followers",
  "niche_keywords": ["pet supplements"],
  "platforms": ["Instagram"],
  "min_followers": None,
  "max_followers": 50000,
  "creator_size_preference": "nano_or_micro"
}
```

Rules:

- If query mentions TikTok, only search TikTok.
- If query mentions Instagram, only search Instagram.
- If query mentions YouTube or Shorts, only search YouTube.
- If no platform is mentioned, search all three.
- If query says “under 50k”, set max_followers = 50000.
- If query says “10k–100k” or “10k-100k”, set min_followers = 10000 and max_followers = 100000.
- If no follower constraint exists, default target is 5k–250k.

This parser can use regex plus simple keyword matching. Do not rely only on the LLM for constraints.

---

## 5. Query planner

File: `src/query_planner.py`

Build a function:

```python
def generate_search_queries(user_query: str, constraints: dict) -> list[str]:
    ...
```

It should call OpenAI using `prompts/query_generation.txt`.

Generate 12 search queries total, including platform-specific searches.

For `dog wellness creators`, generate examples like:

```text
site:tiktok.com/@ dog wellness creator
site:tiktok.com/@ dog allergy tips
site:instagram.com dog wellness creator pet care
site:instagram.com/reel dog supplement review
site:youtube.com/@ dog wellness YouTube creator
site:youtube.com/watch dog allergy tips pet care
"dog wellness" "TikTok"
"pet supplement review" "Instagram"
"dog allergy" "YouTube Shorts"
```

Important:

- Include `site:tiktok.com/@` for TikTok profiles.
- Include `site:instagram.com` and niche terms for Instagram.
- Include `site:youtube.com/@` and `site:youtube.com/watch` for YouTube.
- Include direct niche terms like “dog allergy”, “pet supplements”, “dog wellness”, “pet care”.
- Include “creator”, “influencer”, “review”, “tips”, and “shorts/reels/tiktok” variations.

Do not generate broad queries like just “dog creators”.

---

## 6. Search layer

File: `src/search_serpapi.py`

Implement:

```python
def search_google(query: str, max_results: int = 10) -> list[dict]:
    ...
```

Use SerpAPI endpoint:

```text
https://serpapi.com/search.json
```

Parameters:

```python
{
  "engine": "google",
  "q": query,
  "api_key": SERPAPI_KEY,
  "num": max_results
}
```

Return list of:

```python
{
  "title": "...",
  "link": "...",
  "snippet": "...",
  "source_query": "..."
}
```

Search budget:

- Run up to 12 generated queries.
- Pull 10 results per query.
- Maximum raw links: 120.
- Deduplicate URLs before enrichment.

---

## 7. Platform routing

File: `src/platform_router.py`

Implement:

```python
def detect_platform(url: str) -> str | None:
    ...
```

Rules:

- If URL contains `tiktok.com/@`, return `TikTok`.
- If URL contains `instagram.com`, return `Instagram`.
- If URL contains `youtube.com`, `youtu.be`, or `youtube.com/@`, return `YouTube`.
- Otherwise return None.

Also implement:

```python
def extract_handle_from_url(url: str, platform: str) -> str | None:
    ...
```

Examples:

- `https://www.tiktok.com/@dogmom` → `@dogmom`
- `https://www.instagram.com/dogmom/` → `@dogmom`
- `https://www.youtube.com/@DogWellness` → `@DogWellness`

---

## 8. YouTube enrichment

File: `src/youtube_client.py`

Use YouTube Data API for YouTube results.

Build:

```python
def enrich_youtube_creator(url: str, source_url: str) -> CreatorCandidate | None:
    ...
```

Needed behavior:

1. If URL is a video URL:
   - Extract video ID.
   - Call `videos.list` with `part=snippet,statistics`.
   - Get channel ID from video snippet.
2. If URL is a channel handle URL like `/@handle`:
   - Use YouTube search API or channels endpoint to resolve handle/channel.
3. Call `channels.list` with:
   - `part=snippet,statistics,contentDetails`
   - `id=channel_id`
4. Extract:
   - name = channel title
   - platform = YouTube
   - handle = channel custom URL or title fallback
   - profile_url
   - bio = channel description
   - follower_count = subscriberCount if available
5. Pull recent uploads:
   - Use uploads playlist from `contentDetails.relatedPlaylists.uploads`.
   - Call `playlistItems.list` for latest 5 videos.
   - Capture titles/descriptions.
6. Summarize recent content from latest video titles/descriptions.

Return a `CreatorCandidate`.

Handle hidden subscriber counts by setting follower_count to None but do not crash.

---

## 9. TikTok enrichment through Apify

File: `src/apify_client.py`

Use Apify TikTok scraper actor.

Build:

```python
def enrich_tiktok_creator(url: str, source_url: str) -> CreatorCandidate | None:
    ...
```

Behavior:

1. Extract TikTok handle from URL.
2. Send profile URL or handle to Apify TikTok scraper actor.
3. Request profile metadata and recent posts.
4. Extract:
   - name
   - platform = TikTok
   - handle
   - profile_url
   - bio
   - follower_count
   - recent post captions
   - recent like/comment counts if available
5. Summarize recent content.

If Apify actor output fields differ, inspect the returned JSON and map the closest fields.

Do not hardcode fake profiles.

---

## 10. Instagram enrichment through Apify

File: `src/apify_client.py`

Build:

```python
def enrich_instagram_creator(url: str, source_url: str) -> CreatorCandidate | None:
    ...
```

Behavior:

1. Extract Instagram username from URL.
2. Send profile URL or username to Apify Instagram scraper actor.
3. Request profile metadata and recent posts/reels if available.
4. Extract:
   - name
   - platform = Instagram
   - handle
   - profile_url
   - bio
   - follower_count
   - recent captions
   - recent likes/comments if available
5. Summarize recent content.

If follower count cannot be collected, keep follower_count as None and lower the creator size score later.

---

## 11. Data model

File: `src/models.py`

Use Pydantic or dataclasses. Prefer dataclasses to keep setup simple.

Create:

```python
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

@dataclass
class CreatorCandidate:
    name: str
    platform: str
    handle: str
    profile_url: str
    bio: str
    follower_count: Optional[int]
    recent_content_summary: str
    source_url: str
    recent_posts: List[Dict[str, Any]] = field(default_factory=list)
    avg_likes: Optional[float] = None
    avg_comments: Optional[float] = None
    engagement_rate: Optional[float] = None
    fit_score: Optional[int] = None
    reason: Optional[str] = None
    score_breakdown: Dict[str, Any] = field(default_factory=dict)
```

Final output must include required assignment fields:

```json
{
  "name": "Creator Name",
  "platform": "TikTok / Instagram / YouTube",
  "handle": "@example",
  "profile_url": "https://...",
  "bio": "...",
  "follower_count": 45000,
  "recent_content_summary": "...",
  "source_url": "Where this creator was found",
  "fit_score": 87,
  "reason": "Why this creator is a good fit"
}
```

Optionally include `score_breakdown` for transparency.

---

## 12. Recent content summarization

File: `src/extractor.py`

Implement:

```python
def summarize_recent_content(creator: CreatorCandidate, user_query: str) -> str:
    ...
```

Use OpenAI with `prompts/content_summary.txt`.

Input:

- creator bio
- recent post captions/titles/descriptions
- original user query

Output one concise sentence:

```text
Posts educational dog allergy and pet wellness tips, including supplement reviews and home care advice.
```

If OpenAI fails, fallback to a simple concatenation of the top 3 post titles/captions.

---

## 13. Scoring system

File: `src/scorer.py`

The document recommends heuristics like category relevance, audience fit, creator size, engagement quality, content quality, brand safety, and commercial fit. Implement those exact categories as a transparent weighted score.

Total score = 100 points.

```text
Category relevance: 30 points
Audience fit:       15 points
Creator size:       10 points
Engagement quality: 20 points
Content quality:    10 points
Commercial fit:     10 points
Brand safety:        5 points
```

Do not ask the LLM to simply invent the final score. Use deterministic sub-scores wherever possible, and use the LLM only for judgment-heavy categories.

---

### 13.1 Category relevance, 0–30

Goal: Does the creator consistently post about the target niche?

Inputs:

- user query
- creator bio
- recent content summary
- recent post captions/titles

Implementation:

Use keyword matching plus LLM judgment.

Keyword score:

- Extract niche keywords from query.
- Count matches in bio + content summary + recent posts.
- Award up to 15 points.

LLM relevance score:

- Ask LLM to rate niche relevance from 0–15.
- Criteria:
  - 13–15: directly focused on the niche
  - 9–12: often posts about adjacent niche
  - 5–8: occasionally related
  - 0–4: mostly unrelated

Final relevance = keyword_score + llm_relevance_score.

Cap at 30.

---

### 13.2 Audience fit, 0–15

Goal: Would this creator’s audience likely care about the brand/product?

Use LLM score 0–15.

Criteria:

- 13–15: audience clearly matches buyer profile.
- 9–12: audience probably overlaps.
- 5–8: weak overlap.
- 0–4: poor audience fit.

For pet wellness examples:

- dog owners, pet parents, allergy content, supplement education = high score.
- general comedy/lifestyle with one dog video = low/medium score.

---

### 13.3 Creator size, 0–10

Goal: Prefer creators who are reachable and useful for outreach.

Default target: micro creators, usually 10k–100k followers.

Rules:

```python
if follower_count is None:
    size_score = 4
elif 10_000 <= follower_count <= 100_000:
    size_score = 10
elif 5_000 <= follower_count < 10_000:
    size_score = 8
elif 100_000 < follower_count <= 250_000:
    size_score = 8
elif 1_000 <= follower_count < 5_000:
    size_score = 6
elif 250_000 < follower_count <= 1_000_000:
    size_score = 5
else:
    size_score = 3
```

If user specified follower constraints, also apply a penalty:

- If creator is outside explicit max/min follower range, subtract 4 from size_score.
- Do not go below 0.

---

### 13.4 Engagement quality, 0–20

Goal: Estimate whether audience engagement is strong relative to size.

If likes/comments/follower_count are available:

```python
engagement_rate = (avg_likes + avg_comments) / follower_count
```

Scoring:

```python
if engagement_rate is None:
    engagement_score = 10
elif engagement_rate >= 0.08:
    engagement_score = 20
elif engagement_rate >= 0.05:
    engagement_score = 17
elif engagement_rate >= 0.03:
    engagement_score = 14
elif engagement_rate >= 0.015:
    engagement_score = 10
elif engagement_rate >= 0.005:
    engagement_score = 6
else:
    engagement_score = 3
```

If only partial data exists:

- Use available likes/comments from recent posts.
- If follower count missing, ask LLM for a conservative engagement estimate based on visible stats, but cap score at 12.

---

### 13.5 Content quality, 0–10

Goal: Is the content clear, useful, authentic, and persuasive?

Use LLM score 0–10.

Criteria:

- 9–10: clear, helpful, trustworthy, consistent, brand-friendly.
- 7–8: solid content but not exceptional.
- 4–6: inconsistent or generic.
- 0–3: low quality, unclear, spammy, or irrelevant.

---

### 13.6 Commercial fit, 0–10

Goal: Has this creator shown they can naturally recommend products?

Use keyword signals plus LLM.

Positive signals:

- review
- product
- supplement
- affiliate
- discount code
- sponsored
- partnership
- Amazon finds
- “link in bio”
- “use code”
- “I tried”

Scoring:

- 9–10: clear product review or affiliate-style content.
- 6–8: some product recommendation behavior.
- 3–5: could promote products but no clear evidence.
- 0–2: poor commercial fit or anti-brand tone.

---

### 13.7 Brand safety, 0–5

Goal: Avoid obvious outreach risks.

Start with 5 points.

Subtract points for red flags:

- offensive language
- political ragebait unrelated to niche
- medical misinformation
- unsafe health claims
- spammy/scammy behavior
- adult or violent content
- aggressive controversy

Scoring:

- 5: no obvious red flags.
- 3–4: minor concerns.
- 1–2: meaningful concerns.
- 0: obvious brand safety problem.

Use LLM for this but instruct it to be conservative.

---

## 14. Final scoring formula

Implement:

```python
fit_score = (
    relevance_score
    + audience_fit_score
    + creator_size_score
    + engagement_score
    + content_quality_score
    + commercial_fit_score
    + brand_safety_score
)
```

Round to nearest integer.

Also store:

```python
score_breakdown = {
    "relevance": relevance_score,
    "audience_fit": audience_fit_score,
    "creator_size": creator_size_score,
    "engagement_quality": engagement_score,
    "content_quality": content_quality_score,
    "commercial_fit": commercial_fit_score,
    "brand_safety": brand_safety_score,
    "total": fit_score
}
```

---

## 15. Reason generation

After calculating sub-scores, call OpenAI to generate the final reason.

The reason must be 1–2 sentences and must mention concrete evidence.

Good example:

```text
Strong fit because the creator focuses on dog allergy and pet wellness education, has a micro-creator audience, and recent posts include product-style recommendations. Engagement appears healthy relative to size, with no obvious brand safety red flags.
```

Bad example:

```text
This creator is a good fit because they are relevant.
```

Reason must reference:

- niche relevance
- audience fit
- size or engagement
- commercial/product review fit when available
- red flags if any

---

## 16. Deduplication

File: `src/deduper.py`

Deduplicate by:

1. normalized profile URL
2. platform + lowercase handle
3. creator name if profile URL/handle missing

Keep the candidate with the most complete data.

Completeness ranking:

- has follower_count
- has bio
- has recent posts
- has engagement data
- has profile_url

---

## 17. Filtering rules

Before scoring, remove invalid candidates:

- Missing profile URL.
- Missing handle and name.
- Obvious non-creator pages, such as hashtags, search pages, brand pages, or marketplaces.
- URLs that are not TikTok, Instagram, or YouTube.

After scoring, keep all valid creators but rank by score.

If fewer than 10 creators are found:

- Generate broader fallback queries.
- Search all platforms if original query was too narrow.
- Relax follower constraints but mark that in output reason if relevant.

---

## 18. Error handling

The program should not crash if one API fails.

Implement:

- retries with exponential backoff for HTTP 429/500 errors
- timeout for every request
- logging of failed URLs
- skip bad candidates and continue

Create `outputs/errors.log`.

---

## 19. Output writer

File: `src/output_writer.py`

Save two files:

1. `outputs/creators.json`
2. `outputs/creators.csv`

JSON should be pretty printed.

CSV columns:

```text
name,platform,handle,profile_url,bio,follower_count,recent_content_summary,source_url,fit_score,reason
```

Print top 10 creators to terminal.

---

## 20. README requirements

Create a clear `README.md` with:

1. What the project does.
2. Setup instructions.
3. Required API keys.
4. How to run.
5. Example command.
6. Explanation of data sources.
7. Explanation of scraping/enrichment approach.
8. Explanation of scoring heuristics.
9. Tradeoffs made.
10. How to scale it.

---

## 21. Minimum viable run

The project is done when this command works:

```bash
python main.py "dog wellness creators"
```

And produces:

- at least 10 real creators
- valid JSON
- valid CSV
- fit scores
- clear reasons
- no mocked creators

---

## 22. Cursor instruction

When implementing this project, Cursor should:

1. Read this entire file first.
2. Create every file in the structure above.
3. Implement the code module by module.
4. Use real API calls.
5. Avoid placeholder functions unless marked as optional.
6. Make reasonable field mappings for Apify outputs.
7. Add comments explaining non-obvious logic.
8. Keep code simple and runnable.
9. Write a complete README.
10. Make sure `python main.py "dog wellness creators"` is the final test command.
