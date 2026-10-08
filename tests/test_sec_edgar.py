from alerts.models import NewsItem
from alerts.sources.sec_edgar import parse_submissions, score_floor

DATA = {"filings": {"recent": {
    "form": ["8-K", "S-8", "4"],
    "accessionNumber": ["0001045810-26-000101", "0001045810-26-000102", "0001045810-26-000103"],
    "acceptanceDateTime": ["2026-10-08T20:05:00.000Z", "2026-10-08T20:06:00.000Z", "2026-01-01T00:00:00.000Z"],
    "items": ["2.02,9.01", "", ""],
    "primaryDocument": ["nvda-8k.htm", "s8.htm", "f4.xml"],
    "primaryDocDescription": ["8-K", "S-8", "4"],
}}}

SINCE = 1790000000  # 2026-09-21


def test_parse_submissions_filters_forms_and_time():
    items = parse_submissions(DATA, "NVDA", 1045810, SINCE)
    assert [i.id for i in items] == ["sec:0001045810-26-000101"]  # S-8 skipped, Form 4 too old
    it = items[0]
    assert it.title == "NVDA filed 8-K (current report (material event)) — items 2.02,9.01"
    assert it.url == "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000101/nvda-8k.htm"
    assert it.tickers == ["NVDA"]


def test_score_floor():
    earnings = parse_submissions(DATA, "NVDA", 1045810, SINCE)[0]
    assert score_floor(earnings) == 4
    routine = NewsItem(id="sec:x", source="sec", title="", url="", meta={"form": "8-K", "items": "7.01"})
    assert score_floor(routine) == 0
    assert score_floor(NewsItem(id="hn:1", source="hackernews", title="", url="")) == 0
