"""Shared candidate representation produced by every source module."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Candidate:
    source: str            # 'reddit' | 'tiktok' | 'instagram'
    source_id: str
    source_url: str
    media_url: str          # url the downloader should fetch (yt-dlp target for video, direct url for image/gif)
    category: str
    media_type: str = "video"          # 'video' | 'image' | 'gif' -- ranked on separate scales, never merged
    title: str = ""
    author: str = ""
    subreddit: Optional[str] = None
    score: float = 0.0                 # raw engagement (upvotes/views/plays) -- drives the velocity/trending score
    likes: float = 0.0                 # raw likes/upvotes -- separate from `score` for composite engagement scoring
    comments: float = 0.0              # raw comment count, when the source dataset exposes one
    shares: float = 0.0                # raw share count, when the source dataset exposes one
    published_at: Optional[datetime] = None
    trending_score: float = field(default=0.0, init=False)

    def hours_since_published(self) -> float:
        if not self.published_at:
            return 1.0
        now = datetime.now(timezone.utc)
        delta = now - self.published_at
        return max(delta.total_seconds() / 3600.0, 0.25)  # floor so brand-new posts don't get an infinite score
