from datetime import date, datetime, timedelta

import pytest

from alerts import db
from alerts.models import NewsItem
from alerts.options import Chain, OptionQuote, select_contracts, select_expiries
from alerts.options.snapshots import NY, in_market_hours, take_entries, take_follow_ups

TUE = date(2026, 10, 13)
WEEKLY, MONTHLY, LATER = date(2026, 10, 16), date(2026, 11, 13), date(2026, 12, 18)
EXPIRIES = [date(2026, 10, 14), WEEKLY, date(2026, 10, 23), MONTHLY, LATER]
TRADING_DAYS = [date(2026, 10, d) for d in (12, 13, 14, 15, 16, 19)]


def ny(d, h=11, m=0):
    return datetime(d.year, d.month, d.day, h, m, tzinfo=NY)


def make_chain(ticker, expiry, spot=226.9, quoted_at=None, bid=1.0):
    quotes = []
    for strike in (220.0, 222.5, 225.0, 227.5, 230.0):
        for right in "CP":
            sym = f"{ticker}{expiry:%y%m%d}{right}{int(strike * 1000):08d}"
            quotes.append(OptionQuote(sym, expiry, strike, right, bid=bid, ask=bid + 0.05, last=bid, iv=0.3, volume=10, open_interest=100))
    return Chain(ticker, expiry, spot, quoted_at or ny(TUE), quotes)


def test_select_expiries():
    assert select_expiries(EXPIRIES, TUE) == [WEEKLY, MONTHLY]  # Oct 14 is only 1 day out
    assert select_expiries([WEEKLY], TUE) == [WEEKLY]
    assert select_expiries([date(2026, 10, 14)], TUE) == []


def test_select_contracts_two_nearest_strikes_calls_and_puts():
    picked = select_contracts(make_chain("NVDA", WEEKLY, spot=226.9))
    assert [(q.strike, q.right) for q in picked] == [(225.0, "C"), (225.0, "P"), (227.5, "C"), (227.5, "P")]


def test_market_hours():
    assert in_market_hours(ny(TUE, 9, 35)) and in_market_hours(ny(TUE, 15, 57))
    assert not in_market_hours(ny(TUE, 9, 31)) and not in_market_hours(ny(TUE, 16, 0))
    assert not in_market_hours(ny(date(2026, 10, 17), 12))  # Saturday


class FakeChains:
    def __init__(self, fail=(), quoted_at=None, bid=1.0):
        self.fail, self.quoted_at, self.bid = set(fail), quoted_at, bid
        self.calls = []

    def expiries(self, ticker):
        if ticker in self.fail:
            raise RuntimeError("yahoo down")
        return EXPIRIES

    def chain(self, ticker, expiry):
        self.calls.append((ticker, expiry))
        if ticker in self.fail:
            raise RuntimeError("yahoo down")
        return make_chain(ticker, expiry, quoted_at=self.quoted_at, bid=self.bid)


def _queue(conn, *tickers, item_id="hn:1"):
    db.insert_items(conn, [NewsItem(id=item_id, source="hackernews", title="t", url="u")])
    db.enqueue_snapshots(conn, [(item_id, t) for t in tickers])
    conn.execute("UPDATE option_snapshot_queue SET queued_at = %s", (ny(TUE, 10),))  # not the real clock


def _snapshots(conn, kind="entry"):
    return conn.execute(
        'SELECT ticker, expiry, strike, "right", ask FROM option_snapshots WHERE kind = %s ORDER BY 1, 2, 3, 4', (kind,)
    ).fetchall()


def test_entry_snapshot_takes_8_contracts(conn):
    _queue(conn, "NVDA")
    counts = take_entries(conn, FakeChains(), ny(TUE))
    assert counts["entries"] == 1 and counts["contracts"] == 8
    rows = _snapshots(conn)
    assert len(rows) == 8 and {r[1] for r in rows} == {WEEKLY, MONTHLY}
    assert db.open_snapshot_queue(conn) == []


def test_entry_waits_outside_market_hours(conn):
    _queue(conn, "NVDA")
    chains = FakeChains()
    assert take_entries(conn, chains, ny(TUE, 20)) == {"queued": 1, "entries": 0}
    assert chains.calls == [] and len(db.open_snapshot_queue(conn)) == 1


def test_entry_waits_on_a_holiday(conn):
    _queue(conn, "NVDA")
    stale = FakeChains(quoted_at=ny(TUE - timedelta(days=1), 16))  # last trade was yesterday
    assert take_entries(conn, stale, ny(TUE))["stale_tickers"] == 1
    attempts = conn.execute("SELECT attempts, done_at FROM option_snapshot_queue").fetchone()
    assert attempts == (0, None)


def test_failed_entry_is_retried_then_given_up(conn):
    _queue(conn, "NVDA", "AAPL")
    counts = take_entries(conn, FakeChains(fail={"AAPL"}), ny(TUE))
    assert counts["entries"] == 1 and counts["failed"] == 1
    assert [r[1] for r in db.open_snapshot_queue(conn)] == ["AAPL"]
    take_entries(conn, FakeChains(fail={"AAPL"}), ny(TUE) + timedelta(days=7))  # next Tuesday
    assert db.open_snapshot_queue(conn) == []
    row = conn.execute("SELECT attempts, last_error FROM option_snapshot_queue WHERE ticker = 'AAPL'").fetchone()
    assert row[0] == 2 and "yahoo down" in row[1]


def test_follow_ups_on_day_1_and_3_only(conn):
    _queue(conn, "NVDA")
    take_entries(conn, FakeChains(), ny(TUE))
    for day, expected in ((date(2026, 10, 14), 8), (date(2026, 10, 15), 0), (date(2026, 10, 16), 8)):
        counts = take_follow_ups(conn, FakeChains(bid=2.0, quoted_at=ny(day, 15, 44)), TRADING_DAYS, ny(day, 15, 45))
        assert counts.get("contracts", 0) == expected, day
    assert len(_snapshots(conn, "d1")) == 8 and _snapshots(conn, "d1")[0][4] == 2.05
    assert len(_snapshots(conn, "d3")) == 8
    assert take_follow_ups(conn, FakeChains(), TRADING_DAYS, ny(date(2026, 10, 16), 15, 50)) == {"due": 0}


def test_follow_up_skips_expired_contracts(conn):
    _queue(conn, "NVDA")
    take_entries(conn, FakeChains(), ny(TUE))
    # d3 for a Tuesday entry is Friday Oct 16, the weekly's expiry day: still listed.
    late = [date(2026, 10, d) for d in (13, 14, 15, 19)]  # pretend Oct 16 was a holiday
    monday = ny(date(2026, 10, 19), 15, 45)
    counts = take_follow_ups(conn, FakeChains(quoted_at=monday), late, monday)
    assert counts["contracts"] == 4  # only the monthly is still alive
    assert {r[1] for r in _snapshots(conn, "d3")} == {MONTHLY}


def test_follow_ups_need_a_trading_day(conn):
    _queue(conn, "NVDA")
    take_entries(conn, FakeChains(), ny(TUE))
    assert take_follow_ups(conn, FakeChains(), TRADING_DAYS, ny(date(2026, 10, 17), 12)) == {"due": 0}
