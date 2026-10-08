from __future__ import annotations

import calendar
import hashlib
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import quote_plus

import feedparser

from ..models import NewsItem

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
GOOGLE = "https://news.google.com/rss/search?q={query}+when:1d&hl=en-US&gl=US&ceid=US:en"
AGENT = "Mozilla/5.0 (compatible; stock-news-alerts)"
DEFAULT_LOOKBACK = 2 * 3600


def _entry_time(entry) -> Optional[float]:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(t) if t else None


def parse_feed(feed, ticker: str, since: float) -> List[NewsItem]:
    items = []
    for e in feed.entries:
        link, title = e.get("link"), e.get("title")
        ts = _entry_time(e)
        if not link or not title or (ts is not None and ts <= since):
            continue
        items.append(
            NewsItem(
                id="news:" + hashlib.sha1(link.encode()).hexdigest()[:16],
                source="news",
                title=title,
                url=link,
                text=e.get("summary", ""),
                published_at=datetime.fromtimestamp(ts, tz=timezone.utc) if ts else None,
                tickers=[ticker],  # per-ticker feed: the ticker is known even if the headline omits it
            )
        )
    return items


def fetch(stocks: Dict[str, List[str]], since: Optional[float] = None) -> List[NewsItem]:
    since = since or time.time() - DEFAULT_LOOKBACK
    items: Dict[str, NewsItem] = {}
    for ticker, aliases in stocks.items():
        query = quote_plus(" OR ".join([ticker] + aliases[:1]) + " stock")
        for url in (YAHOO.format(ticker=ticker), GOOGLE.format(query=query)):
            feed = feedparser.parse(url, agent=AGENT)
            for item in parse_feed(feed, ticker, since):
                items.setdefault(item.id, item)
    return list(items.values())
