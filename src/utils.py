import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, TypeVar

import requests
import yaml
from dotenv import load_dotenv

T = TypeVar("T")

ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    load_dotenv(ROOT / ".env")


def load_settings() -> Dict[str, Any]:
    path = ROOT / "config" / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def prompts_dir() -> Path:
    return ROOT / "prompts"


def load_prompt(name: str) -> str:
    path = prompts_dir() / name
    return path.read_text(encoding="utf-8")


def outputs_dir() -> Path:
    """Writable directory for JSON/CSV/errors.log.

    Vercel and AWS Lambda deploy the app under a read-only tree (e.g. /var/task);
    use TMPDIR there. Override with OUTPUT_DIR or TAURUS_OUTPUT_DIR.
    """
    custom = (get_env("OUTPUT_DIR") or get_env("TAURUS_OUTPUT_DIR")).strip()
    if custom:
        d = Path(custom).expanduser()
        if not d.is_absolute():
            d = (ROOT / d).resolve()
    elif (
        get_env("VERCEL")
        or get_env("VERCEL_ENV")
        or get_env("AWS_LAMBDA_FUNCTION_NAME")
        or get_env("AWS_EXECUTION_ENV")
    ):
        d = Path(os.environ.get("TMPDIR", "/tmp")) / "taurus-outputs"
    else:
        d = ROOT / "outputs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sanitize_log_message(message: str) -> str:
    """Strip secrets from strings before writing errors.log (URLs often embed APIFY token)."""
    out = re.sub(r"([?&])token=[^&\s\"']+", r"\1token=***REDACTED***", message, flags=re.IGNORECASE)
    out = re.sub(r"Bearer\s+[A-Za-z0-9._-]+", "Bearer ***REDACTED***", out)
    return out


def log_error(message: str) -> None:
    path = outputs_dir() / "errors.log"
    safe = sanitize_log_message(message)
    logging.warning(safe)
    with open(path, "a", encoding="utf-8") as f:
        f.write(safe + "\n")


def _parse_metric_number(num_str: str, suffix: Optional[str] = None) -> float:
    """Parse '2500', '2.5k', '1.2m' → float."""
    n = float(num_str.replace(",", ""))
    s = (suffix or "").strip().lower()
    if s == "k":
        n *= 1000
    elif s == "m":
        n *= 1_000_000
    return n


def parse_query_constraints(raw_query: str) -> Dict[str, Any]:
    """Extract soft constraints using regex and keywords (BUILD §4)."""
    q_lower = raw_query.lower()
    platforms: List[str] = []
    if "tiktok" in q_lower:
        platforms.append("TikTok")
    if "instagram" in q_lower:
        platforms.append("Instagram")
    if "youtube" in q_lower or "shorts" in q_lower:
        platforms.append("YouTube")

    min_followers: Optional[int] = None
    max_followers: Optional[int] = None

    # Ranges like 10k-100k, 10k–100k
    range_m = re.search(
        r"(\d+)\s*k\s*[-–]\s*(\d+)\s*k",
        q_lower,
        re.I,
    )
    if range_m:
        min_followers = int(range_m.group(1)) * 1000
        max_followers = int(range_m.group(2)) * 1000

    # "under 50k", "below 100k"
    under_m = re.search(r"(?:under|below|less than|<)\s*(\d+)\s*k", q_lower, re.I)
    if under_m and max_followers is None:
        max_followers = int(under_m.group(1)) * 1000

    over_m = re.search(r"(?:over|above|more than|>)\s*(\d+)\s*k", q_lower, re.I)
    if over_m and min_followers is None:
        min_followers = int(over_m.group(1)) * 1000

    # Plain "50k followers" style max
    if max_followers is None:
        plain_under = re.search(r"(\d+)\s*k\s*(?:followers|subs|subscribers)?", q_lower)
        if plain_under and ("under" in q_lower or "below" in q_lower):
            max_followers = int(plain_under.group(1)) * 1000

    # Optional: "avg likes at least 5k", "min average comments 50", "engagement rate over 4%"
    min_avg_likes: Optional[float] = None
    for pattern in (
        r"(?:avg|average)\s+likes\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d,.]+)\s*(k|m)?\b",
        r"(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d,.]+)\s*(k|m)?\s*(?:avg|average)\s+likes\b",
        r"min(?:imum)?\s+(?:avg|average)\s+likes\s*([\d,.]+)\s*(k|m)?\b",
    ):
        m = re.search(pattern, q_lower, re.I)
        if m:
            suf = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
            min_avg_likes = _parse_metric_number(m.group(1), suf or None)
            break

    min_avg_comments: Optional[float] = None
    for pattern in (
        r"(?:avg|average)\s+comments\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d,.]+)\s*(k|m)?\b",
        r"min(?:imum)?\s+(?:avg|average)\s+comments\s*([\d,.]+)\s*(k|m)?\b",
    ):
        m = re.search(pattern, q_lower, re.I)
        if m:
            suf = m.group(2) if m.lastindex and m.lastindex >= 2 else ""
            min_avg_comments = _parse_metric_number(m.group(1), suf or None)
            break

    min_engagement_rate: Optional[float] = None
    em = re.search(
        r"(?:engagement(?:\s+rate)?|eng\.?\s*rate)\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d.]+)\s*%",
        q_lower,
        re.I,
    )
    if em:
        min_engagement_rate = float(em.group(1)) / 100.0
    if min_engagement_rate is None:
        em2 = re.search(
            r"(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d.]+)\s*%\s*(?:engagement(?:\s+rate)?|eng\.?\s*rate)\b",
            q_lower,
            re.I,
        )
        if em2:
            min_engagement_rate = float(em2.group(1)) / 100.0
    if min_engagement_rate is None:
        em3 = re.search(
            r"engagement(?:\s+rate)?\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*(0\.\d+)\b",
            q_lower,
            re.I,
        )
        if em3:
            min_engagement_rate = float(em3.group(1))
    if min_engagement_rate is None:
        em4 = re.search(
            r"(?:min(?:imum)?|at least|>|over)\s+engagement(?:\s+rate)?\s*(0\.\d+)\b",
            q_lower,
            re.I,
        )
        if em4:
            min_engagement_rate = float(em4.group(1))

    niche_keywords: List[str] = []
    # Simple tokenization: strip platform/size phrases and use remaining as niche hint
    stripped = raw_query
    for phrase in (
        "tiktok",
        "instagram",
        "youtube",
        "shorts",
        "creators",
        "creator",
        "influencer",
        "influencers",
        "under",
        "over",
        "followers",
        "follower",
        "subs",
        "subscribers",
        "engagement",
        "rate",
        "likes",
        "comments",
        "avg",
        "average",
        "minimum",
        "percent",
    ):
        stripped = re.sub(rf"\b{re.escape(phrase)}\b", " ", stripped, flags=re.I)
    stripped = re.sub(r"\d+\s*k?", " ", stripped, flags=re.I)
    stripped = re.sub(r"\d+\s*%", " ", stripped)
    stripped = re.sub(r"0\.\d+", " ", stripped)
    stripped = re.sub(r"\s+", " ", stripped).strip()
    if stripped:
        niche_keywords = [w for w in re.split(r"[,;]\s*|\s+", stripped) if len(w) > 2][:12]
    if not niche_keywords:
        niche_keywords = [raw_query.strip()[:80]]

    if min_followers is None and max_followers is None:
        creator_size_preference = "micro_default"
    elif max_followers and max_followers <= 50_000:
        creator_size_preference = "nano_or_micro"
    else:
        creator_size_preference = "mixed"

    return {
        "raw_query": raw_query,
        "niche_keywords": niche_keywords,
        "platforms": platforms,
        "min_followers": min_followers,
        "max_followers": max_followers,
        "creator_size_preference": creator_size_preference,
        "min_avg_likes": min_avg_likes,
        "min_avg_comments": min_avg_comments,
        "min_engagement_rate": min_engagement_rate,
    }


def get_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    return v


def retry_http(
    fn: Callable[[], T],
    *,
    max_attempts: int,
    base_delay: float,
    max_delay: float,
    retry_on: Callable[[Exception], bool],
) -> T:
    delay = base_delay
    last_err: Optional[Exception] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 — surface after retries
            last_err = e
            if attempt >= max_attempts or not retry_on(e):
                raise
            time.sleep(min(delay, max_delay) + random.uniform(0, 0.25 * delay))
            delay = min(delay * 2, max_delay)
    raise last_err  # pragma: no cover


def is_retryable_http_error(exc: Exception) -> bool:
    if isinstance(exc, requests.HTTPError):
        resp = exc.response
        if resp is not None and resp.status_code in (429, 500, 502, 503, 504):
            return True
    if isinstance(exc, requests.Timeout):
        return True
    if isinstance(exc, requests.ConnectionError):
        return True
    return False


def safe_json_loads(text: str) -> Any:
    text = text.strip()
    # Strip markdown fences if the model adds them
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)
