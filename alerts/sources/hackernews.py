from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List, Optional

import requests

from ..models import NewsItem

API = "https://hn.algolia.com/api/v1/search_by_date"
DEFAULT_LOOKBACK = 2 * 3600  # first run: look back 2 hours


def parse_hits(hits: List[dict]) -> List[NewsItem]:
    items = []
    for h in hits:
        oid = h.get("objectID")
        title = h.get("title") or ""
        if not oid or not title:
            continue
        hn_url = f"https://news.ycombinator.com/item?id={oid}"
        items.append(
            NewsItem(
                id=f"hn:{oid}",
                source="hackernews",
                title=title,
                url=h.get("url") or hn_url,
                text=h.get("story_text") or "",
                published_at=datetime.fromtimestamp(h.get("created_at_i", 0), tz=timezone.utc),
                meta={"points": h.get("points"), "comments": h.get("num_comments"), "discussion": hn_url},
            )
        )
    return items


def fetch(since: Optional[float] = None, session: Optional[requests.Session] = None) -> List[NewsItem]:
    """Fetch HN stories created since `since` (unix ts)."""
    since = int(since or time.time() - DEFAULT_LOOKBACK)
    http = session or requests
    resp = http.get(
        API,
        params={"tags": "story", "numericFilters": f"created_at_i>{since}", "hitsPerPage": 200},
        timeout=20,
    )
    resp.raise_for_status()
    return parse_hits(resp.json().get("hits", []))
