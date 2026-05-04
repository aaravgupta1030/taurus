import logging
from typing import Any, Dict, List

import requests

from src.utils import get_env, load_settings, log_error, retry_http, is_retryable_http_error

logger = logging.getLogger(__name__)


def search_google(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """SerpAPI Google search (BUILD §6)."""
    key = get_env("SERPAPI_KEY") or get_env("SERPAPI_API_KEY")
    if not key:
        log_error("search_google: SERPAPI_KEY (or SERPAPI_API_KEY) missing")
        return []

    settings = load_settings()
    timeout = settings.get("http_timeout_seconds", 45)
    retry_cfg = settings.get("retry", {})

    params = {
        "engine": "google",
        "q": query,
        "api_key": key,
        "num": max_results,
    }

    def do_req() -> requests.Response:
        r = requests.get(
            "https://serpapi.com/search.json",
            params=params,
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
    except Exception as e:  # noqa: BLE001
        log_error(f"search_google failed for {query!r}: {e}")
        return []

    data = resp.json()
    organic = data.get("organic_results") or []
    out: List[Dict[str, Any]] = []
    for item in organic[:max_results]:
        out.append(
            {
                "title": item.get("title") or "",
                "link": item.get("link") or "",
                "snippet": item.get("snippet") or "",
                "source_query": query,
            }
        )
    return out
