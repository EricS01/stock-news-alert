"""The `label` job: measure what each scored stock did afterwards.

For every (item, ticker) that was scored, find the entry price (the open of the
first 5-minute bar at or after the first score, which is the next regular-session
open if the market was closed), then the close +1, +3 and +5 trading days later,
the same for SPY, and store the returns. Abnormal return (stock - SPY) is a
generated column in the database.

It also stores the publication context: the price of the stock and of SPY at the
first 5-minute bar at or after publication, and both previous closes, so a later
model can see whether the stock had already moved before entry. 5-minute bars age
out after ~60 days, so this is captured early and retried until it's found.

Run with:  python -m alerts.labeler [--dry-run]
"""
from __future__ import annotations

import argparse
import bisect
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import db
from .options import snapshots
from .options.yahoo import YahooChains
from .prices import Bars, Closes, PriceSource, YahooPrices
from .runs import RunFailed, record_run

log = logging.getLogger("alerts.labeler")

JOB = "label"
BENCHMARK = "SPY"
HORIZONS = (1, 3, 5)  # trading days after the entry day
NY = ZoneInfo("America/New_York")
INTRADAY_HISTORY = timedelta(days=58)  # yfinance serves 5-minute bars for ~60 days
GIVE_UP_AFTER = timedelta(days=30)  # stop retrying pairs that still can't be completed
PREV_CLOSE_LOOKBACK = timedelta(days=7)  # covers a long weekend before publication
CLOSE_FINAL_AT = (16, 15)  # NY time after which today's daily close counts as final
WORKERS = 4


@dataclass
class Pending:
    item_id: str
    ticker: str
    first_scored_at: datetime
    entry_ts: Optional[datetime] = None  # set once found, so later runs don't need old 5-minute bars
    entry_price: Optional[float] = None
    spy_entry_price: Optional[float] = None


@dataclass
class ContextPending:
    item_id: str
    ticker: str
    first_scored_at: datetime
    published_at: Optional[datetime]

    @property
    def at(self) -> Optional[datetime]:
        """Publication time, but never after the first score: context is as of the decision."""
        return min(self.published_at, self.first_scored_at) if self.published_at else None

    def fetchable(self, now: datetime) -> bool:
        return self.at is not None and now - self.at <= INTRADAY_HISTORY


def find_entry(bars: Bars, at: datetime) -> Optional[Tuple[datetime, float]]:
    """First bar starting at or after `at`. A bar labeled 10:00 covers 10:00-10:05,
    so an item scored at 10:02 enters at the 10:05 open: no peeking at the past."""
    i = bisect.bisect_left([ts for ts, _ in bars], at)
    return bars[i] if i < len(bars) else None


def final_trading_days(spy_closes: Closes, now: datetime) -> List[date]:
    """Trading days whose close is final (SPY trades every market day)."""
    local = now.astimezone(NY)
    today_final = (local.hour, local.minute) >= CLOSE_FINAL_AT
    return sorted(d for d in spy_closes if d < local.date() or (d == local.date() and today_final))


def label(
    p: Pending,
    bars: Bars,
    spy_bars: Bars,
    closes: Closes,
    spy_closes: Closes,
    trading_days: List[date],
    now: datetime,
) -> dict:
    """Compute one price_outcomes row from price data. Pure: no I/O."""
    row = {
        "item_id": p.item_id,
        "ticker": p.ticker,
        "first_scored_at": p.first_scored_at,
        "entry_ts": p.entry_ts,
        "entry_price": p.entry_price,
        "spy_entry_price": p.spy_entry_price,
        **{f"{k}_{n}d": None for k in ("ret", "spy_ret") for n in HORIZONS},
    }
    if row["entry_ts"] is None:
        entry = find_entry(bars, p.first_scored_at)
        spy_entry = find_entry(spy_bars, entry[0]) if entry else None
        if entry and spy_entry:
            row["entry_ts"], row["entry_price"] = entry
            row["spy_entry_price"] = spy_entry[1]

    if row["entry_ts"] is not None:
        entry_day = row["entry_ts"].astimezone(NY).date()
        after = trading_days[bisect.bisect_right(trading_days, entry_day):]
        for n in HORIZONS:
            if len(after) < n:
                break
            day = after[n - 1]
            if day in closes:
                row[f"ret_{n}d"] = closes[day] / row["entry_price"] - 1
            if day in spy_closes:
                row[f"spy_ret_{n}d"] = spy_closes[day] / row["spy_entry_price"] - 1

    returns = [row[f"{k}_{n}d"] for k in ("ret", "spy_ret") for n in HORIZONS]
    if all(r is not None for r in returns):
        row["status"] = "complete"
    elif now - p.first_scored_at > GIVE_UP_AFTER:
        row["status"] = "expired"
    else:
        row["status"] = "pending"
    return row


def entry_context(c: ContextPending, bars: Bars, spy_bars: Bars, closes: Closes, spy_closes: Closes,
                  now: datetime) -> dict:
    """Compute one row's publication context from price data. Pure: no I/O."""
    row = {"item_id": c.item_id, "ticker": c.ticker, "pub_ts": None, "pub_price": None,
           "spy_pub_price": None, "prev_close": None, "spy_prev_close": None}
    if c.fetchable(now):
        pub = find_entry(bars, c.at)
        spy_pub = find_entry(spy_bars, pub[0]) if pub else None
        if pub and spy_pub:
            row["pub_ts"], row["pub_price"] = pub
            row["spy_pub_price"] = spy_pub[1]
            pub_day = pub[0].astimezone(NY).date()
            days = sorted(spy_closes)
            i = bisect.bisect_left(days, pub_day)
            if i > 0:
                row["prev_close"] = closes.get(days[i - 1])
                row["spy_prev_close"] = spy_closes.get(days[i - 1])

    if all(row[k] is not None for k in ("pub_ts", "prev_close", "spy_prev_close")):
        row["context_status"] = "complete"
    elif c.fetchable(now):
        row["context_status"] = "pending"
    else:  # no publication time, or its bars are gone: keep whatever was found
        row["context_status"] = "unavailable"
    return row


def fetch_prices(source: PriceSource, pending: List[Pending], context: List[ContextPending], now: datetime):
    """Fetch bars and closes for every ticker involved, plus SPY, concurrently."""
    context = [c for c in context if c.fetchable(now)]
    tickers = sorted({p.ticker for p in pending} | {c.ticker for c in context} | {BENCHMARK})
    oldest = min([p.first_scored_at for p in pending] + [c.at for c in context], default=now)
    daily_start = (oldest.astimezone(NY) - PREV_CLOSE_LOOKBACK).date()
    needs_entry = [p for p in pending if p.entry_ts is None]
    needs_bars = [(p.ticker, p.first_scored_at) for p in needs_entry] + [(c.ticker, c.at) for c in context]
    intraday_start = max(min((at for _, at in needs_bars), default=now), now - INTRADAY_HISTORY)
    intraday_tickers = {t for t, _ in needs_bars} | ({BENCHMARK} if needs_bars else set())

    def one(ticker: str):
        try:
            bars = source.intraday_opens(ticker, intraday_start) if ticker in intraday_tickers else []
            return ticker, bars, source.daily_closes(ticker, daily_start)
        except Exception as e:  # one ticker's data shouldn't stop the rest
            log.warning("prices failed for %s: %s", ticker, e)
            return ticker, None, None

    bars: Dict[str, Bars] = {}
    closes: Dict[str, Closes] = {}
    failed: List[str] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for ticker, b, c in pool.map(one, tickers):
            if c is None:
                failed.append(ticker)
            else:
                bars[ticker], closes[ticker] = b, c
    return bars, closes, failed


def label_all(conn, source: PriceSource, now: Optional[datetime] = None, dry_run: bool = False) -> dict:
    now = now or datetime.now(timezone.utc)
    pending = [Pending(*r) for r in db.pending_outcomes(conn)]
    context = [ContextPending(*r) for r in db.pending_context(conn)]
    if not pending and not context:
        return {"pending": 0, "labeled": 0, "complete": 0, "expired": 0}

    t = time.monotonic()
    bars, closes, failed = fetch_prices(source, pending, context, now)
    fetch_s = round(time.monotonic() - t, 1)
    if BENCHMARK in failed:
        raise RunFailed(f"could not fetch {BENCHMARK} prices", {"pending": len(pending), "failed_tickers": failed})

    trading_days = final_trading_days(closes[BENCHMARK], now)
    rows = [
        label(p, bars[p.ticker], bars[BENCHMARK], closes[p.ticker], closes[BENCHMARK], trading_days, now)
        for p in pending
        if p.ticker in closes
    ]
    ctx_rows = [
        entry_context(c, bars.get(c.ticker, []), bars[BENCHMARK], closes.get(c.ticker, {}), closes[BENCHMARK], now)
        for c in context
        if c.ticker in closes or not c.fetchable(now)
    ]
    if not dry_run:
        db.upsert_outcomes(conn, rows)
        db.update_context(conn, ctx_rows)  # after the upsert, so new pairs have a row to update
    for r in rows if dry_run else []:
        log.info("%s %s %s entry=%s ret_1d=%s", r["ticker"], r["item_id"], r["status"], r["entry_price"], r["ret_1d"])
    for r in ctx_rows if dry_run else []:
        log.info("context %s %s %s pub_ts=%s pub=%s spy_pub=%s prev=%s spy_prev=%s", r["ticker"], r["item_id"],
                 r["context_status"], r["pub_ts"], r["pub_price"], r["spy_pub_price"], r["prev_close"],
                 r["spy_prev_close"])

    return {
        "pending": len(pending),
        "labeled": len(rows),
        "complete": sum(r["status"] == "complete" for r in rows),
        "expired": sum(r["status"] == "expired" for r in rows),
        "context": {s: sum(r["context_status"] == s for r in ctx_rows) for s in ("complete", "pending", "unavailable")},
        "failed_tickers": failed,
        "fetch_s": fetch_s,
    }


def snapshot_options(conn, prices: PriceSource, chains, now: datetime) -> dict:
    """Entry snapshots still queued, then +1/+3 trading day re-snapshots. Never raises."""
    today = now.astimezone(NY).date()
    counts = {"entries": snapshots.guarded("entries", snapshots.take_entries, conn, chains, now)}
    try:
        trading_days = sorted(prices.daily_closes(BENCHMARK, today - timedelta(days=15)))
    except Exception as e:
        log.warning("could not get trading days for option follow-ups: %s", e)
        return {**counts, "follow_ups": {"error": str(e)}}
    counts["follow_ups"] = snapshots.guarded("follow-ups", snapshots.take_follow_ups, conn, chains, trading_days, now)
    return counts


def run(dry_run: bool = False) -> None:
    if not os.environ.get("DATABASE_URL"):
        raise RunFailed("DATABASE_URL is not set")
    with db.connect() as conn:
        if dry_run:  # computes and logs, writes nothing
            log.info("dry run counts: %s", label_all(conn, YahooPrices(), dry_run=True))
        else:
            def work() -> dict:
                prices = YahooPrices()
                counts = label_all(conn, prices)
                counts["options"] = snapshot_options(conn, prices, YahooChains(), datetime.now(timezone.utc))
                return counts

            record_run(conn, JOB, work)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Label scored items with what the stock did afterwards")
    parser.add_argument("--dry-run", action="store_true", help="log outcomes instead of writing them")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(dry_run=args.dry_run)
