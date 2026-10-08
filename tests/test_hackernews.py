import json
from pathlib import Path

from alerts.sources.hackernews import parse_hits

FIXTURE = Path(__file__).parent / "fixtures" / "hn_hits.json"


def test_parse_hits():
    items = parse_hits(json.loads(FIXTURE.read_text())["hits"])
    assert [i.id for i in items] == ["hn:101", "hn:102"]  # untitled hit dropped
    assert items[0].url == "https://example.com/nvda"
    # Ask HN posts fall back to the discussion URL
    assert items[1].url == "https://news.ycombinator.com/item?id=102"
    assert items[1].text == "Share your projects"
