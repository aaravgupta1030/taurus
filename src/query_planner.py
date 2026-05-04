import json
import logging
import re
from typing import Any, Dict, List

from openai import OpenAI

from src.utils import get_env, load_prompt, safe_json_loads

logger = logging.getLogger(__name__)


def generate_platform_balance_queries(user_query: str, constraints: Dict[str, Any]) -> List[str]:
    """
    Deterministic high-recall queries so one SERP wave is not all Instagram.
    Runs before LLM-generated queries.
    """
    niche = (" ".join(constraints.get("niche_keywords") or []) or user_query).strip()[:72]
    if not niche:
        niche = "creator"
    plat = constraints.get("platforms") or []
    out: List[str] = []
    if not plat or "TikTok" in plat:
        out.extend(
            [
                f'site:tiktok.com/@ {niche} creator tips',
                f'site:tiktok.com/@ "{niche}" wellness influencer',
                f'site:tiktok.com/@ {niche} vet pet health',
            ]
        )
    if not plat or "YouTube" in plat:
        out.extend(
            [
                f'site:youtube.com/@ {niche} creator channel',
                f'site:youtube.com/@ "{niche}" tips weekly',
                f'site:youtube.com/watch {niche} full episode',
            ]
        )
    if not plat or "Instagram" in plat:
        out.extend(
            [
                f"site:instagram.com {niche} creator influencer",
                f"site:instagram.com/reel {niche} tips",
            ]
        )
    return out[:12]


def _fallback_queries(user_query: str, constraints: Dict[str, Any]) -> List[str]:
    niche = " ".join(constraints.get("niche_keywords") or [])[:80]
    plat_note = constraints.get("platforms") or []
    tik = "TikTok" in plat_note or not plat_note
    ig = "Instagram" in plat_note or not plat_note
    yt = "YouTube" in plat_note or not plat_note
    queries: List[str] = []
    if tik:
        queries.extend(
            [
                f'site:tiktok.com/@ "{niche}" creator',
                f'site:tiktok.com/@ {niche} tips influencer',
            ]
        )
    if ig:
        queries.extend(
            [
                f"site:instagram.com {niche} creator influencer",
                f"site:instagram.com/reel {niche} review",
            ]
        )
    if yt:
        queries.extend(
            [
                f'site:youtube.com/@ "{niche}" creator',
                f'site:youtube.com/watch {niche} tips',
            ]
        )
    queries.extend(
        [
            f'"{niche}" "TikTok"',
            f'"{niche}" "Instagram"',
            f'"{niche}" "YouTube Shorts"',
        ]
    )
    while len(queries) < 16:
        queries.append(f"{user_query} creator site:instagram.com")
    return queries[:16]


def generate_search_queries(
    user_query: str,
    constraints: Dict[str, Any],
    *,
    fallback: bool = False,
) -> List[str]:
    """
    Use OpenAI with prompts/query_generation.txt; return 16 Google-style queries.
    """
    template = load_prompt("query_generation.txt")
    key = get_env("OPENAI_API_KEY")
    if not key:
        logger.warning("OPENAI_API_KEY missing; using regex fallback queries")
        return _fallback_queries(user_query, constraints)

    client = OpenAI(api_key=key)
    extra = ""
    if fallback:
        extra = (
            "\nFALLBACK MODE: queries were too narrow. Use slightly broader niche terms "
            "but still include site: operators. Do not only output a single generic keyword."
        )
    if constraints.get("platforms"):
        extra += f"\nUser asked to focus on: {', '.join(constraints['platforms'])}. "

    user_block = f"User query: {user_query}\n"
    user_block += f"Parsed constraints JSON: {json.dumps(constraints, ensure_ascii=False)}\n"
    user_block += extra

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            messages=[
                {"role": "system", "content": template},
                {"role": "user", "content": user_block},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("OpenAI query generation failed: %s", e)
        return _fallback_queries(user_query, constraints)

    # Try JSON array
    try:
        if text.startswith("["):
            data = safe_json_loads(text)
            if isinstance(data, list) and all(isinstance(x, str) for x in data):
                qlist = [x.strip() for x in data if x.strip()]
                if len(qlist) >= 8:
                    while len(qlist) < 16:
                        qlist.append(qlist[-1])
                    return qlist[:16]
    except Exception:  # noqa: BLE001
        pass

    # Numbered or line list
    lines = [re.sub(r"^\d+[\).]\s*", "", ln).strip() for ln in text.splitlines() if ln.strip()]
    lines = [ln for ln in lines if len(ln) > 3]
    if len(lines) >= 8:
        while len(lines) < 16:
            lines.append(lines[-1])
        return lines[:16]

    return _fallback_queries(user_query, constraints)


def _fallback_expansion_queries(user_query: str, constraints: Dict[str, Any]) -> List[str]:
    niche = " ".join(constraints.get("niche_keywords") or [])[:60]
    plat = constraints.get("platforms") or []
    out: List[str] = []
    if not plat or "TikTok" in plat:
        out.extend(
            [
                f'site:tiktok.com/@ {niche} tips daily routine creator',
                f'site:tiktok.com/@ {niche} vet nutrition series',
            ]
        )
    if not plat or "Instagram" in plat:
        out.extend(
            [
                f"site:instagram.com {niche} micro influencer tips weekly",
                f"site:instagram.com/reel {niche} education not viral",
            ]
        )
    if not plat or "YouTube" in plat:
        out.extend(
            [
                f'site:youtube.com/@ {niche} weekly tips creator',
                f'site:youtube.com/watch {niche} full guide series',
            ]
        )
    out.append(f'"{niche}" consistent posting creator micro')
    out.append(f'site:youtube.com/@ {niche} small channel high engagement')
    while len(out) < 8:
        out.append(out[-1])
    return out[:8]


def generate_recall_expansion_queries(user_query: str, constraints: Dict[str, Any]) -> List[str]:
    """Second-pass queries biased toward steady niche creators (higher recall)."""
    template = load_prompt("recall_expansion_queries.txt")
    key = get_env("OPENAI_API_KEY")
    if not key:
        return _fallback_expansion_queries(user_query, constraints)

    client = OpenAI(api_key=key)
    user_block = (
        f"User query: {user_query}\n"
        f"Parsed constraints JSON: {json.dumps(constraints, ensure_ascii=False)}\n"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.45,
            messages=[
                {"role": "system", "content": template},
                {"role": "user", "content": user_block},
            ],
        )
        text = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        logger.warning("OpenAI expansion queries failed: %s", e)
        return _fallback_expansion_queries(user_query, constraints)

    try:
        if text.startswith("["):
            data = safe_json_loads(text)
            if isinstance(data, list) and all(isinstance(x, str) for x in data):
                qlist = [x.strip() for x in data if x.strip()]
                if len(qlist) >= 6:
                    while len(qlist) < 8:
                        qlist.append(qlist[-1])
                    return qlist[:8]
    except Exception:  # noqa: BLE001
        pass

    return _fallback_expansion_queries(user_query, constraints)
