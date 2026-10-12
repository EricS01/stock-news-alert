import json

from alerts.models import NewsItem
from alerts.scorer import build_prompt, make_batches, parse_results


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


def test_make_batches_keeps_an_items_pairs_together():
    a = NewsItem(id="a", source="news", title="t", url="u")
    b = NewsItem(id="b", source="news", title="t", url="u")
    c = NewsItem(id="c", source="news", title="t", url="u")
    pairs = [(a, "NVDA"), (a, "AMD"), (b, "AAPL"), (b, "MSFT"), (b, "GOOGL"), (c, "TSLA")]
    batches = make_batches(pairs, size=4)
    assert [[(i.id, t) for i, t in batch] for batch in batches] == [
        [("a", "NVDA"), ("a", "AMD")],
        [("b", "AAPL"), ("b", "MSFT"), ("b", "GOOGL"), ("c", "TSLA")],
    ]
    assert make_batches([]) == []
