from datetime import date, datetime, timedelta, timezone

import pytest

from alerts import db
from alerts.models import NewsItem
from alerts.report import REPORT_DIR, build_and_publish, fmt, markdown_table, parse_header, run_sections

NOW = datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=7)


def test_parse_header():
    assert parse_header("-- title: By score\n-- note: All data.\nSELECT 1", "x") == ("By score", "All data.")
    assert parse_header("SELECT 1", "02_x") == ("02_x", "")


def test_fmt_and_table():
    assert [fmt(v) for v in (None, 3, 1.5, 2.0, "a|b")] == ["–", "3", "1.5", "2", "a\\|b"]
    assert markdown_table(["a_b"], []) == "_No data yet._"
    assert markdown_table(["a_b", "c"], [(1, None)]).splitlines() == ["| a b | c |", "|---|---|", "| 1 | – |"]


def test_queries_have_no_literal_percent():
    for path in REPORT_DIR.glob("*.sql"):
        assert "%" not in path.read_text().replace("%(since)s", ""), path.name


def _outcome(conn, item_id, ticker, score, direction, abn_1d, alerted=False):
    db.insert_items(conn, [NewsItem(id=item_id, source="news", title=f"title {item_id}", url="u")])
    db.insert_scores(conn, [dict(item_id=item_id, ticker=ticker, prompt_version="v1", score=score, direction=direction,
                                 reason="", floor_applied=False, alerted=alerted)])
    conn.execute("UPDATE scores SET scored_at = %s WHERE item_id = %s", (NOW - timedelta(days=2), item_id))
    db.upsert_outcomes(conn, [dict(item_id=item_id, ticker=ticker, first_scored_at=NOW - timedelta(days=2),
                                   entry_ts=NOW - timedelta(days=2), entry_price=100.0, spy_entry_price=500.0,
                                   ret_1d=abn_1d, ret_3d=abn_1d * 2, ret_5d=None,
                                   spy_ret_1d=0.0, spy_ret_3d=0.0, spy_ret_5d=None, status="pending")])


@pytest.fixture
def data(conn):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    # Bigger scores, bigger moves: perfect rank correlation.
    _outcome(conn, "a", "NVDA", 1, "neutral", 0.001)
    _outcome(conn, "b", "AAPL", 2, "bullish", -0.004)
    _outcome(conn, "c", "MSFT", 4, "bullish", 0.02, alerted=True)
    _outcome(conn, "d", "TSLA", 5, "bearish", 0.05, alerted=True)  # wrong way
    return conn


def _section(sections, title_start):
    return next(s for s in sections if s.title.startswith(title_start))


def test_by_score_and_correlation(data):
    sections = run_sections(data, SINCE)
    by_score = {r[0]: r for r in _section(sections, "By score").rows}
    assert by_score[4][3] == pytest.approx(2.0)  # avg |abn 1d| in percent
    assert by_score[4][5] == 100 and by_score[5][5] == 0  # hit rate 1d
    assert by_score[1][5] is None  # neutral calls have no direction to hit
    rho = {r[0]: r for r in _section(sections, "Does a higher score").rows}
    assert rho["1 day"][1] == 4 and float(rho["1 day"][2]) == pytest.approx(1.0)
    misses = _section(sections, "Worst misses").rows
    assert [(r[0], r[2]) for r in misses] == [("false alarm", "MSFT"), ("false alarm", "TSLA"),
                                              ("missed mover", "AAPL"), ("missed mover", "NVDA")]


def _option(conn, item_id, ticker, right, strike, kind, bid, ask, expiry=date(2026, 10, 23), spot=100.4):
    db.insert_option_snapshots(conn, [dict(
        item_id=item_id, ticker=ticker, contract_symbol=f"{ticker}{expiry:%y%m%d}{right}{int(strike * 1000):08d}",
        expiry=expiry, strike=strike, right=right, kind=kind, taken_at=NOW - timedelta(days=2),
        underlying_price=spot, bid=bid, ask=ask, last=None, iv=None, volume=None, open_interest=None)])


def test_options_returns_buy_at_ask_sell_at_bid(data):
    for right in "CP":
        for strike in (100.0, 102.5):  # 100 is nearest the 100.4 stock price
            _option(data, "c", "MSFT", right, strike, "entry", bid=1.9, ask=2.0)
    _option(data, "c", "MSFT", "C", 100.0, "d1", bid=3.0, ask=3.1)  # call doubled-ish
    _option(data, "c", "MSFT", "P", 100.0, "d1", bid=0.5, ask=0.6)
    rows = {(r[0], r[1]): r for r in _section(run_sections(data, SINCE), "Options").rows}
    direction = rows[("direction", "weekly")]  # bullish -> the 100 call: 3.0 / 2.0 - 1
    assert direction[3] == 1 and direction[4] == 1 and float(direction[5]) == pytest.approx(50.0)
    straddle = rows[("straddle", "weekly")]  # (3.0 + 0.5) / (2.0 + 2.0) - 1
    assert float(straddle[5]) == pytest.approx(-12.5)
    assert straddle[6] == 0  # no d3 yet


class FakeNotifier:
    def __init__(self):
        self.messages = []

    def send_message(self, title, message, click=None):
        self.messages.append((title, message, click))


def test_build_and_publish_writes_summary_and_pushes(data, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    notifier = FakeNotifier()
    counts = build_and_publish(data, notifier, now=NOW)
    text = summary.read_text()
    assert text.startswith("# Weekly research report") and "## By score" in text and "| score |" in text
    assert counts["sections"] == len(list(REPORT_DIR.glob("[0-9][0-9]_*.sql")))
    title, message, click = notifier.messages[0]
    assert title == "Weekly report: 4 scores, 2 alerts"
    assert "3.5% for scores 4-5" in message and "Alerts moving the predicted way: 50%" in message
    assert click == "https://github.com/o/r/actions/runs/42"


def test_report_on_an_empty_database(conn):
    notifier = FakeNotifier()
    build_and_publish(conn, notifier, now=NOW, dry_run=True)
    assert notifier.messages[0][1] == "No price outcomes yet."
