from datetime import date, datetime, timedelta, timezone

import pytest

from alerts import db
from alerts.labeler import (NY, ContextPending, Pending, entry_context, final_trading_days, find_entry, label,
                            label_all)
from alerts.models import NewsItem
from alerts.runs import RunFailed

# Thu Oct 8 2026 ... Thu Oct 15 2026 (weekend Oct 10-11)
DAYS = [date(2026, 10, d) for d in (8, 9, 12, 13, 14, 15)]


def ny(d, h, m):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY)


def bars(opens_by_day):
    """Three 5-minute bars per day (09:30, 10:00, 10:05) with the given open."""
    return [(ny(d, h, m), o) for d, o in opens_by_day.items() for h, m in ((9, 30), (10, 0), (10, 5))]


STOCK_BARS = bars({d: 100.0 + i for i, d in enumerate(DAYS)})
SPY_BARS = bars({d: 500.0 for d in DAYS})
STOCK_CLOSES = {d: 110.0 + i for i, d in enumerate(DAYS)}  # 110, 111, 112, 113, 114, 115
SPY_CLOSES = {d: 505.0 for d in DAYS}
AFTER_ALL_CLOSES = ny(date(2026, 10, 15), 17, 0)


def _label(scored_at, now=AFTER_ALL_CLOSES, **kw):
    p = Pending("hn:1", "NVDA", scored_at, **kw)
    return label(p, STOCK_BARS, SPY_BARS, STOCK_CLOSES, SPY_CLOSES, final_trading_days(SPY_CLOSES, now), now)


def test_entry_is_the_next_bar_not_the_one_in_progress():
    assert find_entry(STOCK_BARS, ny(DAYS[0], 10, 2)) == (ny(DAYS[0], 10, 5), 100.0)
    assert find_entry(STOCK_BARS, ny(DAYS[0], 10, 0)) == (ny(DAYS[0], 10, 0), 100.0)
    assert find_entry(STOCK_BARS, ny(DAYS[-1], 16, 0)) is None


def test_returns_at_each_horizon():
    row = _label(ny(DAYS[0], 10, 2))  # Thursday
    assert row["entry_price"] == 100.0 and row["spy_entry_price"] == 500.0
    assert row["ret_1d"] == pytest.approx(111 / 100 - 1)  # Fri close
    assert row["ret_3d"] == pytest.approx(113 / 100 - 1)  # Tue close (skips the weekend)
    assert row["ret_5d"] == pytest.approx(115 / 100 - 1)  # next Thu close
    assert row["spy_ret_5d"] == pytest.approx(0.01)
    assert row["status"] == "complete"


def test_scored_over_the_weekend_enters_at_monday_open():
    row = _label(ny(date(2026, 10, 10), 12, 0))  # Saturday
    assert row["entry_ts"] == ny(DAYS[2], 9, 30)
    assert row["entry_price"] == 102.0
    assert row["ret_1d"] == pytest.approx(113 / 102 - 1)  # Tuesday
    assert row["ret_5d"] is None and row["status"] == "pending"


def test_todays_close_is_not_used_before_it_is_final():
    label_run = ny(DAYS[3], 15, 45)  # Tuesday, while the market is still open
    row = _label(ny(DAYS[0], 10, 2), now=label_run)
    assert row["ret_1d"] is not None  # Friday
    assert row["ret_3d"] is None  # Tuesday's close isn't final yet
    assert row["status"] == "pending"


def test_stored_entry_is_reused():
    row = _label(ny(DAYS[0], 10, 2), entry_ts=ny(DAYS[0], 9, 30), entry_price=50.0, spy_entry_price=250.0)
    assert row["entry_price"] == 50.0 and row["ret_1d"] == pytest.approx(111 / 50 - 1)


def test_gives_up_after_30_days():
    now = AFTER_ALL_CLOSES + timedelta(days=40)
    p = Pending("hn:1", "NVDA", ny(DAYS[0], 10, 2))
    row = label(p, [], [], {}, {}, [], now)  # no data at all
    assert row["status"] == "expired" and row["entry_ts"] is None


def _context(published_at, scored_at=None, now=AFTER_ALL_CLOSES):
    c = ContextPending("hn:1", "NVDA", scored_at or ny(DAYS[-1], 10, 0), published_at)
    return entry_context(c, STOCK_BARS, SPY_BARS, STOCK_CLOSES, SPY_CLOSES, now)


def test_context_is_the_bar_after_publication_and_the_close_before_it():
    row = _context(ny(DAYS[1], 10, 2))  # Friday
    assert row["pub_ts"] == ny(DAYS[1], 10, 5)
    assert (row["pub_price"], row["spy_pub_price"]) == (101.0, 500.0)
    assert (row["prev_close"], row["spy_prev_close"]) == (110.0, 505.0)  # Thursday
    assert row["context_status"] == "complete"


def test_published_over_the_weekend_uses_monday_open_and_friday_close():
    row = _context(ny(date(2026, 10, 10), 12, 0))
    assert row["pub_ts"] == ny(DAYS[2], 9, 30) and row["pub_price"] == 102.0
    assert row["prev_close"] == 111.0


def test_publication_after_the_first_score_uses_the_score_time():
    row = _context(ny(DAYS[3], 10, 2), scored_at=ny(DAYS[1], 10, 2))
    assert row["pub_ts"] == ny(DAYS[1], 10, 5)


def test_context_waits_for_bars_that_dont_exist_yet():
    row = _context(ny(DAYS[-1], 16, 30), scored_at=ny(DAYS[-1], 16, 40))  # after the last bar
    assert row["pub_ts"] is None and row["context_status"] == "pending"


def test_context_unavailable_without_publication_time_or_once_bars_age_out():
    assert _context(None)["context_status"] == "unavailable"
    old = _context(ny(DAYS[1], 10, 2), now=AFTER_ALL_CLOSES + timedelta(days=60))
    assert old["context_status"] == "unavailable" and old["pub_ts"] is None  # never matched to a later bar


class FakePrices:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls = []

    def intraday_opens(self, ticker, start):
        self.calls.append(("intraday", ticker))
        if ticker in self.fail:
            raise RuntimeError("yahoo down")
        return SPY_BARS if ticker == "SPY" else STOCK_BARS

    def daily_closes(self, ticker, start):
        self.calls.append(("daily", ticker))
        if ticker in self.fail:
            raise RuntimeError("yahoo down")
        return SPY_CLOSES if ticker == "SPY" else STOCK_CLOSES


def _seed(conn, scored_at, published_at=None):
    db.ensure_prompt_version(conn, "v1", "m", "p")
    db.insert_items(conn, [NewsItem(id="hn:1", source="hackernews", title="t", url="u", published_at=published_at)])
    rows = [dict(item_id="hn:1", ticker=t, prompt_version="v1", score=3, direction="neutral",
                 reason="", floor_applied=False, alerted=False) for t in ("NVDA", "AAPL")]
    db.insert_scores(conn, rows)
    conn.execute("UPDATE scores SET scored_at = %s", (scored_at,))


def test_label_all_writes_outcomes_and_finishes(conn):
    _seed(conn, ny(DAYS[0], 10, 2))
    counts = label_all(conn, FakePrices(), now=ny(DAYS[3], 17, 0))  # Tue after close: 1d and 3d final
    assert counts["labeled"] == 2 and counts["complete"] == 0
    row = conn.execute(
        "SELECT entry_price, ret_1d, ret_3d, ret_5d, abn_1d, status FROM price_outcomes WHERE ticker = 'NVDA'"
    ).fetchone()
    assert row[0] == 100.0 and row[3] is None and row[5] == "pending"
    assert row[4] == pytest.approx((111 / 100 - 1) - (505 / 500 - 1))  # generated column

    prices = FakePrices()
    counts = label_all(conn, prices, now=AFTER_ALL_CLOSES)
    assert counts["complete"] == 2
    assert ("intraday", "NVDA") not in prices.calls  # entry already stored
    assert label_all(conn, FakePrices(), now=AFTER_ALL_CLOSES)["pending"] == 0


def test_one_ticker_failing_leaves_it_pending(conn):
    _seed(conn, ny(DAYS[0], 10, 2))
    counts = label_all(conn, FakePrices(fail={"AAPL"}), now=AFTER_ALL_CLOSES)
    assert counts["failed_tickers"] == ["AAPL"] and counts["labeled"] == 1
    assert [r[0] for r in db.pending_outcomes(conn)] == ["hn:1"]


def test_benchmark_failing_fails_the_run(conn):
    _seed(conn, ny(DAYS[0], 10, 2))
    with pytest.raises(RunFailed, match="SPY"):
        label_all(conn, FakePrices(fail={"SPY"}), now=AFTER_ALL_CLOSES)


def test_dry_run_writes_nothing(conn):
    _seed(conn, ny(DAYS[0], 10, 2))
    label_all(conn, FakePrices(), now=AFTER_ALL_CLOSES, dry_run=True)
    assert conn.execute("SELECT count(*) FROM price_outcomes").fetchone()[0] == 0


CONTEXT_SQL = "SELECT pub_ts, pub_price, prev_close, spy_prev_close, context_status FROM price_outcomes WHERE ticker = 'NVDA'"


def test_label_all_captures_context_in_the_same_run(conn):
    _seed(conn, ny(DAYS[1], 10, 20), published_at=ny(DAYS[1], 10, 2))
    counts = label_all(conn, FakePrices(), now=AFTER_ALL_CLOSES)
    assert counts["context"] == {"complete": 2, "pending": 0, "unavailable": 0}
    assert conn.execute(CONTEXT_SQL).fetchone() == (ny(DAYS[1], 10, 5), 101.0, 110.0, 505.0, "complete")
    assert db.pending_context(conn) == []


def test_finished_rows_are_backfilled_without_touching_returns(conn):
    _seed(conn, ny(DAYS[1], 10, 20), published_at=ny(DAYS[1], 10, 2))
    label_all(conn, FakePrices(), now=AFTER_ALL_CLOSES)
    returns = conn.execute("SELECT ret_1d, ret_3d, status FROM price_outcomes ORDER BY ticker").fetchall()
    # as existing rows look right after the migration
    conn.execute("UPDATE price_outcomes SET pub_ts = NULL, pub_price = NULL, spy_pub_price = NULL,"
                 " prev_close = NULL, spy_prev_close = NULL, context_status = 'pending'")

    counts = label_all(conn, FakePrices(fail={"AAPL"}), now=AFTER_ALL_CLOSES)
    assert counts["context"]["complete"] == 1
    assert conn.execute(CONTEXT_SQL).fetchone()[-1] == "complete"
    assert [r[0] for r in db.pending_context(conn)] == ["hn:1"]  # AAPL retried next run
    assert conn.execute("SELECT ret_1d, ret_3d, status FROM price_outcomes ORDER BY ticker").fetchall() == returns


def test_aging_out_keeps_what_was_found(conn):
    _seed(conn, ny(DAYS[1], 10, 20), published_at=ny(DAYS[1], 10, 2))
    label_all(conn, FakePrices(), now=AFTER_ALL_CLOSES)
    conn.execute("UPDATE price_outcomes SET prev_close = NULL, context_status = 'pending'")
    label_all(conn, FakePrices(), now=AFTER_ALL_CLOSES + timedelta(days=60))
    assert conn.execute(CONTEXT_SQL).fetchone() == (ny(DAYS[1], 10, 5), 101.0, None, 505.0, "unavailable")
