# Tradeoffs

- **Search-first discovery**: Google results vary by region and personalization proxies; SerpAPI normalizes much of this, but daily variance still exists.
- **Platform asymmetry**: YouTube offers a first-party API with structured statistics; TikTok and Instagram rely on Apify actors whose schemas can drift when platforms change markup or rate limits tighten.
- **LLM scoring**: Judgment categories use OpenAI for transparency of rationale; scores are constrained by rubric JSON to reduce arbitrary numbers.
- **Budget caps**: The pipeline stops after a bounded number of searches and links to avoid runaway cost; highly niche queries may return fewer than ten creators unless constraints are relaxed.
- **Handle-only routing**: Some Instagram reels or TikTok video permutations without clear handles are skipped early to avoid scraping error pages.
