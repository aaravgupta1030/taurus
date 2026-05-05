import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, TypeVar

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


def _parse_follower_amount(num_str: str, suffix: Optional[str] = None) -> int:
    """Parse audience size tokens into integer followers (e.g. 12.5k → 12500)."""
    n = float(num_str.replace(",", ""))
    s = (suffix or "").strip().lower()
    if s in ("k", "thousand", "thousands"):
        n *= 1000
    elif s in ("m", "million", "millions"):
        n *= 1_000_000
    return max(0, int(round(n)))


def _normalize_engagement_fraction(val: Any) -> Optional[float]:
    """LLM/user may give 0.05 or 5 (meaning 5%); store as 0–1 fraction."""
    if val is None:
        return None
    try:
        x = float(val)
    except (TypeError, ValueError):
        return None
    if x > 1.0:
        x = x / 100.0
    return max(0.0, min(1.0, x))


def _sanitize_constraint_bounds(d: Dict[str, Any]) -> None:
    """If min > max for the same metric, swap endpoints (handles reversed wording)."""
    pairs = [
        ("min_followers", "max_followers"),
        ("min_avg_likes", "max_avg_likes"),
        ("min_avg_comments", "max_avg_comments"),
        ("min_engagement_rate", "max_engagement_rate"),
    ]
    for lk, hk in pairs:
        lo, hi = d.get(lk), d.get(hk)
        if lo is None or hi is None:
            continue
        try:
            lf, hf = float(lo), float(hi)
        except (TypeError, ValueError):
            continue
        if lf > hf:
            d[lk], d[hk] = hi, lo


def _fetch_constraints_openai_primary(user_query: str) -> Optional[Dict[str, Any]]:
    """Parse the user query with OpenAI JSON output; returns None if no key or request fails."""
    key = get_env("OPENAI_API_KEY")
    if not key:
        return None
    try:
        from openai import OpenAI

        client = OpenAI(api_key=key)
        template = load_prompt("constraint_parsing.txt")
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": template},
                {"role": "user", "content": f"USER QUERY:\n{user_query.strip()}\n"},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
        data = safe_json_loads(text)
        return data if isinstance(data, dict) else None
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning("OpenAI constraint parse failed: %s", e)
        return None


def _apply_llm_constraints_over_regex_fallback(
    llm: Optional[Dict[str, Any]],
    raw_query: str,
    regex_base: Dict[str, Any],
) -> Dict[str, Any]:
    """Start from regex baseline; overlay any non-null fields from the LLM (primary source)."""
    out = dict(regex_base)
    out["raw_query"] = raw_query
    if not llm:
        return out

    nk = llm.get("niche_keywords")
    if isinstance(nk, list):
        cleaned = [str(x).strip() for x in nk if str(x).strip()]
        if cleaned:
            out["niche_keywords"] = cleaned[:12]

    plat = llm.get("platforms")
    if isinstance(plat, list):
        allowed = {"TikTok", "Instagram", "YouTube"}
        filt = [p for p in plat if p in allowed]
        if filt:
            out["platforms"] = filt

    for key in ("min_followers", "max_followers"):
        v = llm.get(key)
        if v is not None:
            try:
                out[key] = int(round(float(v)))
            except (TypeError, ValueError):
                pass

    for key in ("min_avg_likes", "max_avg_likes", "min_avg_comments", "max_avg_comments"):
        v = llm.get(key)
        if v is not None:
            try:
                out[key] = float(v)
            except (TypeError, ValueError):
                pass

    me = _normalize_engagement_fraction(llm.get("min_engagement_rate"))
    if me is not None:
        out["min_engagement_rate"] = me
    xe = _normalize_engagement_fraction(llm.get("max_engagement_rate"))
    if xe is not None:
        out["max_engagement_rate"] = xe

    return out


def _follower_bounds_from_regex(q_lower: str) -> Tuple[Optional[int], Optional[int]]:
    """Best-effort min/max follower counts from free text."""
    min_followers: Optional[int] = None
    max_followers: Optional[int] = None

    def set_min(v: int) -> None:
        nonlocal min_followers
        if min_followers is None:
            min_followers = v
        else:
            min_followers = max(min_followers, v)

    def set_max(v: int) -> None:
        nonlocal max_followers
        if max_followers is None:
            max_followers = v
        else:
            max_followers = min(max_followers, v)

    # Ranges: 10k-100k, 10k–100k (optional k/m on each side)
    range_m = re.search(
        r"(\d[\d,]*(?:\.\d+)?)\s*(k|m)?\s*[-–]\s*(\d[\d,]*(?:\.\d+)?)\s*(k|m)?(?:\s*(?:followers|subs|subscribers))?",
        q_lower,
        re.I,
    )
    if range_m:
        lo = _parse_follower_amount(range_m.group(1), range_m.group(2))
        hi = _parse_follower_amount(range_m.group(3), range_m.group(4))
        if lo <= hi:
            return lo, hi

    between_m = re.search(
        r"between\s+(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)?\s+and\s+(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)?",
        q_lower,
        re.I,
    )
    if between_m:
        g2, g4 = between_m.group(2), between_m.group(4)
        tail = q_lower[between_m.end() : between_m.end() + 48]
        has_scale = bool((g2 or "").strip() or (g4 or "").strip())
        has_audience_word = bool(re.search(r"\b(followers|subs|subscribers|fans)\b", tail))
        # "avg comments between 10 and 100" has no k/m and no followers — not a follower band
        if has_scale or has_audience_word:
            lo = _parse_follower_amount(between_m.group(1), between_m.group(2))
            hi = _parse_follower_amount(between_m.group(3), between_m.group(4))
            if lo <= hi:
                return lo, hi

    # Upper bound with k/m/thousand/million — safe without the word "followers"
    for m in re.finditer(
        r"(?:under|below|less than|fewer than|no more than|at most)\s+(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)\b",
        q_lower,
        re.I,
    ):
        set_max(_parse_follower_amount(m.group(1), m.group(2)))

    # Plain integers only when audience words follow (avoids "less than 10% engagement")
    for m in re.finditer(
        r"(?:under|below|fewer than|no more than|at most)\s+(\d[\d,]+)\s*(?=\s*(?:followers|subs|subscribers|fans)\b)",
        q_lower,
        re.I,
    ):
        set_max(_parse_follower_amount(m.group(1), None))

    for m in re.finditer(
        r"(?:max|maximum)\s+(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)?(?=\s*(?:followers|subs|subscribers|fans|\)|,|$))",
        q_lower,
        re.I,
    ):
        suf = m.group(2) if m.lastindex >= 2 else None
        set_max(_parse_follower_amount(m.group(1), suf))

    # Lower bound with scale
    for m in re.finditer(
        r"(?:over|above|greater than|more than)\s+(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)\b",
        q_lower,
        re.I,
    ):
        set_min(_parse_follower_amount(m.group(1), m.group(2)))

    for m in re.finditer(
        r"(?:at least|min(?:imum)?)\s+(\d[\d,]*(?:\.\d+)?)\s*(k|m|thousand|million)?(?=\s*(?:followers|subs|subscribers|fans|\)|,|$))",
        q_lower,
        re.I,
    ):
        suf = m.group(2) if m.lastindex >= 2 else None
        set_min(_parse_follower_amount(m.group(1), suf))

    for m in re.finditer(
        r"(?:over|above|more than)\s+(\d[\d,]+)\s*(?=\s*(?:followers|subs|subscribers|fans)\b)",
        q_lower,
        re.I,
    ):
        set_min(_parse_follower_amount(m.group(1), None))

    # "<10000 followers"
    lt_m = re.search(
        r"<\s*(\d[\d,]+)\s*(?=\s*(?:followers|subs|subscribers|fans)\b)",
        q_lower,
        re.I,
    )
    if lt_m:
        set_max(_parse_follower_amount(lt_m.group(1), None))

    return min_followers, max_followers


def _post_metric_bounds_from_regex(q_lower: str) -> Dict[str, Optional[float]]:
    """Min/max for avg likes & comments and engagement rate (fraction 0–1)."""
    out: Dict[str, Optional[float]] = {
        "min_avg_likes": None,
        "max_avg_likes": None,
        "min_avg_comments": None,
        "max_avg_comments": None,
        "min_engagement_rate": None,
        "max_engagement_rate": None,
    }

    def smin(k: str, v: float) -> None:
        cur = out[k]
        out[k] = v if cur is None else max(cur, v)

    def smax(k: str, v: float) -> None:
        cur = out[k]
        out[k] = v if cur is None else min(cur, v)

    lrange = re.search(
        r"(?:avg|average)\s+likes\s+between\s+([\d,.]+)\s*(k|m)?\s+and\s+([\d,.]+)\s*(k|m)?",
        q_lower,
        re.I,
    )
    if lrange:
        lo = _parse_metric_number(lrange.group(1), lrange.group(2) or None)
        hi = _parse_metric_number(lrange.group(3), lrange.group(4) or None)
        if lo <= hi:
            smin("min_avg_likes", lo)
            smax("max_avg_likes", hi)

    crange = re.search(
        r"(?:avg|average)\s+comments\s+between\s+([\d,.]+)\s*(k|m)?\s+and\s+([\d,.]+)\s*(k|m)?",
        q_lower,
        re.I,
    )
    if crange:
        lo = _parse_metric_number(crange.group(1), crange.group(2) or None)
        hi = _parse_metric_number(crange.group(3), crange.group(4) or None)
        if lo <= hi:
            smin("min_avg_comments", lo)
            smax("max_avg_comments", hi)

    erng = re.search(
        r"engagement(?:\s+rate)?\s+between\s+([\d.]+)\s*%?\s+and\s+([\d.]+)\s*%?",
        q_lower,
        re.I,
    )
    if erng:
        a, b = float(erng.group(1)), float(erng.group(2))
        if a > 1.0:
            a /= 100.0
        if b > 1.0:
            b /= 100.0
        lo, hi = (a, b) if a <= b else (b, a)
        smin("min_engagement_rate", lo)
        smax("max_engagement_rate", hi)

    for pattern in (
        r"(?:avg|average)\s+likes\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d,.]+)\s*(k|m)?\b",
        r"(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d,.]+)\s*(k|m)?\s*(?:avg|average)\s+likes\b",
        r"min(?:imum)?\s+(?:avg|average)\s+likes\s*([\d,.]+)\s*(k|m)?\b",
    ):
        for m in re.finditer(pattern, q_lower, re.I):
            suf = m.group(2) if m.lastindex >= 2 else ""
            smin("min_avg_likes", _parse_metric_number(m.group(1), suf or None))

    for pattern in (
        r"(?:avg|average)\s+likes\s*(?:<|≤|under|below|less than|at most|no more than|max(?:imum)?)\s*([\d,.]+)\s*(k|m)?\b",
        r"(?:under|below|less than|at most|no more than)\s*([\d,.]+)\s*(k|m)?\s*(?:avg|average)\s+likes\b",
        r"max(?:imum)?\s+(?:avg|average)\s+likes\s*(?:of\s*)?([\d,.]+)\s*(k|m)?\b",
    ):
        for m in re.finditer(pattern, q_lower, re.I):
            suf = m.group(2) if m.lastindex >= 2 else ""
            smax("max_avg_likes", _parse_metric_number(m.group(1), suf or None))

    for pattern in (
        r"(?:avg|average)\s+comments\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d,.]+)\s*(k|m)?\b",
        r"min(?:imum)?\s+(?:avg|average)\s+comments\s*([\d,.]+)\s*(k|m)?\b",
    ):
        for m in re.finditer(pattern, q_lower, re.I):
            suf = m.group(2) if m.lastindex >= 2 else ""
            smin("min_avg_comments", _parse_metric_number(m.group(1), suf or None))

    for pattern in (
        r"(?:avg|average)\s+comments\s*(?:<|≤|under|below|less than|at most|no more than|max(?:imum)?)\s*([\d,.]+)\s*(k|m)?\b",
        r"(?:under|below|less than|at most|no more than)\s*([\d,.]+)\s*(k|m)?\s*(?:avg|average)\s+comments\b",
        r"max(?:imum)?\s+(?:avg|average)\s+comments\s*(?:of\s*)?([\d,.]+)\s*(k|m)?\b",
    ):
        for m in re.finditer(pattern, q_lower, re.I):
            suf = m.group(2) if m.lastindex >= 2 else ""
            smax("max_avg_comments", _parse_metric_number(m.group(1), suf or None))

    for pattern in (
        r"(?:engagement(?:\s+rate)?|eng\.?\s*rate)\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d.]+)\s*%",
        r"(?:>|≥|>=|at least|min(?:imum)?|over)\s*([\d.]+)\s*%\s*(?:engagement(?:\s+rate)?|eng\.?\s*rate)\b",
    ):
        for m in re.finditer(pattern, q_lower, re.I):
            smin("min_engagement_rate", float(m.group(1)) / 100.0)

    for pattern in (
        r"engagement(?:\s+rate)?\s*(?:>|≥|>=|at least|min(?:imum)?|over)\s*(0\.\d+)\b",
        r"(?:min(?:imum)?|at least|>|over)\s+engagement(?:\s+rate)?\s*(0\.\d+)\b",
    ):
        for m in re.finditer(pattern, q_lower, re.I):
            smin("min_engagement_rate", float(m.group(1)))

    for pattern in (
        r"(?:engagement(?:\s+rate)?|eng\.?\s*rate)\s*(?:<|≤|under|below|less than|at most|no more than|max)\s*([\d.]+)\s*%",
        r"(?:under|below|less than|at most|no more than)\s*([\d.]+)\s*%\s*(?:engagement(?:\s+rate)?|eng\.?\s*rate)\b",
        r"max(?:imum)?\s+(?:engagement(?:\s+rate)?|eng\.?\s*rate)\s*(?:of\s*)?([\d.]+)\s*%",
        r"max(?:imum)?\s+([\d.]+)\s*%\s*(?:engagement(?:\s+rate)?|eng\.?\s*rate)\b",
    ):
        for m in re.finditer(pattern, q_lower, re.I):
            smax("max_engagement_rate", float(m.group(1)) / 100.0)

    for m in re.finditer(
        r"engagement(?:\s+rate)?\s*(?:<|≤|under|below|at most)\s*(0\.\d+)\b",
        q_lower,
        re.I,
    ):
        smax("max_engagement_rate", float(m.group(1)))

    return out


def _build_regex_constraint_baseline(raw_query: str) -> Dict[str, Any]:
    """Regex + keyword fallback when OpenAI is unavailable or omits a field."""
    q_lower = raw_query.lower()
    platforms: List[str] = []
    if "tiktok" in q_lower:
        platforms.append("TikTok")
    if "instagram" in q_lower:
        platforms.append("Instagram")
    if "youtube" in q_lower or "shorts" in q_lower:
        platforms.append("YouTube")

    min_followers: Optional[int]
    max_followers: Optional[int]
    min_followers, max_followers = _follower_bounds_from_regex(q_lower)
    mtr = _post_metric_bounds_from_regex(q_lower)
    min_avg_likes = mtr.get("min_avg_likes")
    max_avg_likes = mtr.get("max_avg_likes")
    min_avg_comments = mtr.get("min_avg_comments")
    max_avg_comments = mtr.get("max_avg_comments")
    min_engagement_rate = mtr.get("min_engagement_rate")
    max_engagement_rate = mtr.get("max_engagement_rate")

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

    base = {
        "raw_query": raw_query,
        "niche_keywords": niche_keywords,
        "platforms": platforms,
        "min_followers": min_followers,
        "max_followers": max_followers,
        "creator_size_preference": "micro_default",
        "min_avg_likes": min_avg_likes,
        "max_avg_likes": max_avg_likes,
        "min_avg_comments": min_avg_comments,
        "max_avg_comments": max_avg_comments,
        "min_engagement_rate": min_engagement_rate,
        "max_engagement_rate": max_engagement_rate,
    }
    if min_followers is None and max_followers is None:
        base["creator_size_preference"] = "micro_default"
    elif max_followers is not None and max_followers <= 50_000:
        base["creator_size_preference"] = "nano_or_micro"
    else:
        base["creator_size_preference"] = "mixed"

    return base


def parse_query_constraints(raw_query: str) -> Dict[str, Any]:
    """Prefer OpenAI JSON when OPENAI_API_KEY is set; regex fills nulls and backs up if the API fails."""
    regex_base = _build_regex_constraint_baseline(raw_query)
    merged = regex_base
    if get_env("OPENAI_API_KEY"):
        llm = _fetch_constraints_openai_primary(raw_query)
        merged = _apply_llm_constraints_over_regex_fallback(llm, raw_query, regex_base)
    _sanitize_constraint_bounds(merged)
    mf = merged.get("min_followers")
    xf = merged.get("max_followers")
    if mf is None and xf is None:
        merged["creator_size_preference"] = "micro_default"
    elif xf is not None and xf <= 50_000:
        merged["creator_size_preference"] = "nano_or_micro"
    else:
        merged["creator_size_preference"] = "mixed"
    return merged


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
