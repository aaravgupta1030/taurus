import csv
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from src.models import CreatorCandidate
from src.utils import outputs_dir

logger = logging.getLogger(__name__)


def write_outputs(creators: List[CreatorCandidate]) -> Dict[str, Path]:
    out_dir = outputs_dir()
    json_path = out_dir / "creators.json"
    csv_path = out_dir / "creators.csv"

    rows: List[Dict[str, Any]] = []
    for c in creators:
        rows.append(
            {
                "name": c.name,
                "platform": c.platform,
                "handle": c.handle,
                "profile_url": c.profile_url,
                "bio": c.bio,
                "follower_count": c.follower_count,
                "recent_content_summary": c.recent_content_summary,
                "source_url": c.source_url,
                "fit_score": c.fit_score,
                "reason": c.reason,
                "score_breakdown": c.score_breakdown,
            }
        )

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

    fieldnames = [
        "name",
        "platform",
        "handle",
        "profile_url",
        "bio",
        "follower_count",
        "recent_content_summary",
        "source_url",
        "fit_score",
        "reason",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for c in creators:
            w.writerow(
                {
                    "name": c.name,
                    "platform": c.platform,
                    "handle": c.handle,
                    "profile_url": c.profile_url,
                    "bio": c.bio,
                    "follower_count": c.follower_count,
                    "recent_content_summary": c.recent_content_summary,
                    "source_url": c.source_url,
                    "fit_score": c.fit_score,
                    "reason": (c.reason or "").replace("\n", " "),
                }
            )

    return {"json": json_path, "csv": csv_path}


def print_top_creators(creators: List[CreatorCandidate], n: int = 10) -> None:
    """Print top N creators to terminal (BUILD §19)."""
    top = sorted(
        creators,
        key=lambda c: (c.fit_score or 0),
        reverse=True,
    )[:n]
    for i, c in enumerate(top, 1):
        print(f"\n--- #{i} — {c.name} ({c.platform}) score={c.fit_score} ---")
        print(f"    {c.handle} | {c.profile_url}")
        print(f"    {c.reason}")
