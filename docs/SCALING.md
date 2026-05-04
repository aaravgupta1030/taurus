# Scaling ideas

- **Horizontal fan-out**: Parallelize SerpAPI queries and enrichment jobs with a worker queue; respect per-provider rate limits with token buckets.
- **Caching**: Persist normalized profile payloads keyed by `(platform, handle)` to skip repeated Apify runs within a TTL window.
- **Incremental scoring**: Short-circuit obviously irrelevant creators using cheap keyword gates before calling LLM scoring.
- **Dedicated datasets**: For stable internal lists, maintain allowlisted creator seeds per vertical and blend with live search results.
- **Observability**: Ship structured logs (latency, HTTP status, actor success rate) to your monitoring stack to tune concurrency and budgets.
