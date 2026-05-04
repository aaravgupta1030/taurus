from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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
