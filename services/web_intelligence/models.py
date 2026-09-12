from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class WebResult:
    title: str = ""
    url: str = ""
    snippet: str = ""
    source: str = ""
    score: float = 0.0
    published_at: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "source": self.source,
            "score": self.score,
            "published_at": self.published_at,
        }


@dataclass
class WebPage:
    url: str
    title: str = ""
    text: str = ""
    status_code: int = 0
    source: str = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "text": self.text[:4000],
            "status_code": self.status_code,
            "source": self.source,
            "error": self.error,
        }
