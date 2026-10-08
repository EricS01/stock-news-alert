from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Iterable, List, Optional

import praw

from ..models import NewsItem

SUBREDDITS = "stocks+investing+wallstreetbets+StockMarket"
DEFAULT_LOOKBACK = 2 * 3600


def parse_submissions(posts: Iterable, since: float) -> List[NewsItem]:
    items = []
    for p in posts:
        if p.created_utc <= since or getattr(p, "stickied", False):
            continue
        permalink = f"https://www.reddit.com{p.permalink}"
        items.append(
            NewsItem(
                id=f"reddit:{p.id}",
                source="reddit",
                title=p.title,
                url=permalink,
                text=(p.selftext or "")[:1000],
                published_at=datetime.fromtimestamp(p.created_utc, tz=timezone.utc),
                meta={"subreddit": str(p.subreddit), "score": p.score, "link": p.url},
            )
        )
    return items


def fetch(since: Optional[float] = None) -> List[NewsItem]:
    since = since or time.time() - DEFAULT_LOOKBACK
    reddit = praw.Reddit(
        client_id=os.environ["REDDIT_CLIENT_ID"],
        client_secret=os.environ["REDDIT_CLIENT_SECRET"],
        user_agent="stock-news-alerts/0.1 (personal project)",
    )
    reddit.read_only = True
    return parse_submissions(reddit.subreddit(SUBREDDITS).new(limit=200), since)
