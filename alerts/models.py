from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class NewsItem:
    """A single piece of news from any source, normalized."""

    id: str  # globally unique, prefixed by source, e.g. "hn:12345"
    source: str  # "hackernews", "news", "sec", "reddit"
    title: str
    url: str
    text: str = ""
    published_at: Optional[datetime] = None
    tickers: List[str] = field(default_factory=list)  # filled by matcher
    meta: dict = field(default_factory=dict)  # source-specific extras


@dataclass
class Alert:
    item: NewsItem
    ticker: str
    score: int  # 1-5 impact
    direction: str  # bullish / bearish / neutral
    reason: str
