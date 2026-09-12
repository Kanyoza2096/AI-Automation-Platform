from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class NormalizedTrend:
    title: str
    content: str = ""
    url: str = ""
    author: str = ""
    platform: str = "unknown"
    engagement_count: int = 0
    published_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "author": self.author,
            "platform": self.platform,
            "engagement_count": self.engagement_count,
            "published_at": self.published_at,
        }


class TrendSourceAdapter(ABC):
    platform: str = "unknown"

    @abstractmethod
    def fetch(self, query: str, **kwargs: Any) -> List[NormalizedTrend]:
        ...

    def is_configured(self) -> bool:
        return True
