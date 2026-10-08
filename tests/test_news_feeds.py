import time

import feedparser

from alerts.matcher import Matcher
from alerts.sources.news_feeds import parse_feed

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>Chip stocks slide on export rules</title><link>https://n.test/1</link>
<pubDate>Thu, 08 Oct 2026 15:00:00 GMT</pubDate></item>
<item><title>Old story</title><link>https://n.test/2</link>
<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_feed_filters_old_and_pretags_ticker():
    feed = feedparser.parse(RSS)
    since = time.mktime((2026, 1, 1, 0, 0, 0, 0, 0, 0))
    items = parse_feed(feed, "NVDA", since)
    assert [i.title for i in items] == ["Chip stocks slide on export rules"]
    assert items[0].tickers == ["NVDA"]
    # matcher keeps the pre-tagged ticker even though the headline doesn't name it
    assert Matcher({"NVDA": ["Nvidia"], "AAPL": ["Apple"]}).match(items[0]) == ["NVDA"]
