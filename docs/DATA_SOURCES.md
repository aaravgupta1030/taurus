# Data sources

The Creator Sourcing Agent combines programmatic APIs with search-assisted discovery.

## SerpAPI (Google Search)

- Used to surface candidate TikTok, Instagram, and YouTube URLs using curated queries produced by the query planner.
- Limited by budget in `config/settings.yaml` (queries × results per query).

## YouTube Data API v3

- Resolves watch URLs to channels, reads channel metadata and subscriber counts when exposed.
- Reads recent uploads via each channel's uploads playlist id.

## OpenAI API

- Builds discovery queries, summarizes recent content themes, contributes structured scoring judgments where deterministic signals are insufficient, and generates human-readable fit reasons.

## Apify

- Runs third-party **Actor** scrapers (defaults: `clockworks/tiktok-profile-scraper` and `apify/instagram-scraper`) to obtain public profile and post information for TikTok and Instagram. Actor input/output fields can change; the client maps the closest available fields to the internal model.
