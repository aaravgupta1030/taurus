import logging
from typing import List

from openai import OpenAI

from src.models import CreatorCandidate
from src.utils import get_env, load_prompt, log_error

logger = logging.getLogger(__name__)


def summarize_recent_content(creator: CreatorCandidate, user_query: str) -> str:
    """OpenAI summary using prompts/content_summary.txt; concat fallback (BUILD §12)."""
    key = get_env("OPENAI_API_KEY")
    template = load_prompt("content_summary.txt")

    parts: List[str] = []
    if creator.bio:
        parts.append(f"Bio: {creator.bio[:1200]}")
    caps = []
    for p in creator.recent_posts[:8]:
        if isinstance(p, dict):
            t = p.get("caption") or p.get("title") or p.get("description")
            if t:
                caps.append(str(t)[:400])
    if caps:
        parts.append("Recent posts:\n" + "\n".join(caps))

    ctx = "\n".join(parts)
    if not key:
        log_error("summarize_recent_content: missing OPENAI_API_KEY")
        return _fallback_summary(creator)

    client = OpenAI(api_key=key)
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.3,
            messages=[
                {"role": "system", "content": template},
                {
                    "role": "user",
                    "content": f"User / brand query: {user_query}\n\nCreator context:\n{ctx}",
                },
            ],
        )
        out = (resp.choices[0].message.content or "").strip()
        if out:
            return out[:800]
    except Exception as e:  # noqa: BLE001
        log_error(f"summarize_recent_content OpenAI error: {e}")

    return _fallback_summary(creator)


def _fallback_summary(creator: CreatorCandidate) -> str:
    bits: List[str] = []
    for p in creator.recent_posts[:3]:
        if not isinstance(p, dict):
            continue
        t = p.get("title") or p.get("caption") or p.get("description")
        if t:
            bits.append(str(t)[:200])
    if bits:
        return "Recent themes: " + " | ".join(bits)
    return (creator.bio or creator.name)[:500]
