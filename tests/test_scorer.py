import json

from alerts.models import NewsItem
from alerts.scorer import build_prompt, parse_results


def _pairs():
    a = NewsItem(id="hn:1", source="hackernews", title="Nvidia under DOJ antitrust probe", url="u1")
    b = NewsItem(id="hn:2", source="hackernews", title="Self-hosting Google Fonts", url="u2")
    return [(a, "NVDA"), (b, "GOOGL")]


def test_build_prompt_numbers_pairs():
    p = build_prompt(_pairs())
    assert "[0] ticker=NVDA" in p and "[1] ticker=GOOGL" in p


def test_parse_results_maps_indexes_and_clamps():
    text = json.dumps({"results": [
        {"index": 0, "score": 9, "direction": "bearish", "reason": "Antitrust probe"},
        {"index": 1, "score": 1, "direction": "neutral", "reason": "Passing mention"},
        {"index": 7, "score": 5, "direction": "bullish", "reason": "bogus index"},
    ]})
    alerts = parse_results(text, _pairs())
    assert [(a.ticker, a.score) for a in alerts] == [("NVDA", 5), ("GOOGL", 1)]
    assert alerts[0].direction == "bearish"
