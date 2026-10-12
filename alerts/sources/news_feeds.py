from __future__ import annotations

import calendar
import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import feedparser
import requests

from ..models import NewsItem

log = logging.getLogger(__name__)

YAHOO = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={ticker}&region=US&lang=en-US"
GOOGLE = "https://news.google.com/rss/search?q={query}+when:1d&hl=en-US&gl=US&ceid=US:en"
AGENT = "Mozilla/5.0 (compatible; stock-news-alerts)"
DEFAULT_LOOKBACK = 2 * 3600
WORKERS = 8  # two feeds per ticker, fetched concurrently
TIMEOUT = 15


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


def _fetch_feed(url: str, ticker: str, since: float) -> List[NewsItem]:
    # Fetch with requests rather than feedparser so a slow feed can't hang the run.
    try:
        resp = requests.get(url, headers={"User-Agent": AGENT}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        log.warning("feed failed for %s: %s", ticker, e)
        return []
    return parse_feed(feedparser.parse(resp.content), ticker, since)


def feed_urls(stocks: Dict[str, List[str]]) -> List[Tuple[str, str]]:
    urls = []
    for ticker, aliases in stocks.items():
        query = quote_plus(" OR ".join([ticker] + aliases[:1]) + " stock")
        urls += [(YAHOO.format(ticker=ticker), ticker), (GOOGLE.format(query=query), ticker)]
    return urls


def fetch(stocks: Dict[str, List[str]], since: Optional[float] = None) -> List[NewsItem]:
    since = since or time.time() - DEFAULT_LOOKBACK
    items: Dict[str, NewsItem] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = pool.map(lambda job: _fetch_feed(job[0], job[1], since), feed_urls(stocks))
        for batch in results:  # in watchlist order, so the first ticker to list an item keeps it
            for item in batch:
                items.setdefault(item.id, item)
    return list(items.values())
