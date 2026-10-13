"""Entry snapshots for alerts (queued until market hours) and +1/+3 day re-snapshots.

Snapshot failures are logged and retried; they never fail the job that runs them.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .. import db
from . import Chain, ChainSource, OptionQuote, select_contracts, select_expiries

log = logging.getLogger(__name__)

NY = ZoneInfo("America/New_York")
# Skip the first minutes after the open, when spreads are widest, and the last ones.
OPEN_FROM, OPEN_UNTIL = (9, 35), (15, 58)
GIVE_UP_AFTER = timedelta(days=3)  # stop retrying an entry snapshot after this long
FOLLOW_UPS = {1: "d1", 3: "d3"}  # trading days after the entry snapshot -> kind
WORKERS = 4


def in_market_hours(now: datetime) -> bool:
    """Weekday between 9:35 and 15:58 New York time. Holidays are caught later,
    because a chain's last trade is then from a previous day."""
    local = now.astimezone(NY)
    return local.weekday() < 5 and OPEN_FROM <= (local.hour, local.minute) < OPEN_UNTIL


def is_live(chain: Chain, now: datetime) -> bool:
    return chain.quoted_at.astimezone(NY).date() == now.astimezone(NY).date()


def snapshot_rows(item_id: str, ticker: str, kind: str, chain: Chain, quotes: List[OptionQuote], now: datetime) -> List[dict]:
    return [
        {
            "item_id": item_id,
            "ticker": ticker,
            "contract_symbol": q.contract_symbol,
            "expiry": q.expiry,
            "strike": q.strike,
            "right": q.right,
            "kind": kind,
            "taken_at": now,
            "underlying_price": chain.underlying_price,
            "bid": q.bid,
            "ask": q.ask,
            "last": q.last,
            "iv": q.iv,
            "volume": q.volume,
            "open_interest": q.open_interest,
        }
        for q in quotes
    ]


def guarded(name: str, fn, *args) -> dict:
    """Run a snapshot step; log and report failures instead of raising."""
    try:
        return fn(*args)
    except Exception as e:
        log.warning("options %s failed: %s", name, e)
        return {"error": f"{type(e).__name__}: {e}"}


def take_entries(conn, source: ChainSource, now: datetime) -> dict:
    """Snapshot 8 near-the-money contracts for every queued alert, if the market is open."""
    queue = db.open_snapshot_queue(conn)  # (item_id, ticker, queued_at)
    if not queue:
        return {"queued": 0}
    if not in_market_hours(now):
        return {"queued": len(queue), "entries": 0}
    today = now.astimezone(NY).date()
    by_ticker: Dict[str, List[tuple]] = defaultdict(list)
    for row in queue:
        by_ticker[row[1]].append(row)

    def one(ticker: str) -> Tuple[str, Optional[List[Chain]], Optional[str]]:
        try:
            expiries = select_expiries(source.expiries(ticker), today)
            if not expiries:
                return ticker, None, "no listed expiries"
            return ticker, [source.chain(ticker, e) for e in expiries], None
        except Exception as e:
            return ticker, None, f"{type(e).__name__}: {e}"

    rows: List[dict] = []
    done: List[tuple] = []
    failed: List[tuple] = []  # (item_id, ticker, error, give_up)
    closed = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for ticker, chains, error in pool.map(one, sorted(by_ticker)):
            if chains is not None and not all(is_live(c, now) for c in chains):
                closed += 1  # market holiday: quotes are stale, so wait without counting an attempt
                continue
            for item_id, _, queued_at in by_ticker[ticker]:
                if chains is None:
                    log.warning("option entry snapshot failed for %s %s: %s", ticker, item_id, error)
                    failed.append((item_id, ticker, error, now - queued_at > GIVE_UP_AFTER))
                    continue
                for chain in chains:
                    rows += snapshot_rows(item_id, ticker, "entry", chain, select_contracts(chain), now)
                done.append((item_id, ticker))

    with conn.transaction():
        db.insert_option_snapshots(conn, rows)
        db.finish_snapshot_queue(conn, done, failed)
    return {"queued": len(queue), "entries": len(done), "contracts": len(rows), "failed": len(failed), "stale_tickers": closed}


def take_follow_ups(conn, source: ChainSource, trading_days: List[date], now: datetime) -> dict:
    """Re-snapshot each entry's contracts 1 and 3 trading days later.

    `trading_days` must include today if the market is open. A day that's missed
    (no label run) is skipped rather than taken late, so every d1/d3 is comparable.
    """
    today = now.astimezone(NY).date()
    if today not in trading_days or not in_market_hours(now):
        return {"due": 0}
    index = {d: i for i, d in enumerate(sorted(trading_days))}
    groups: Dict[Tuple[str, str], dict] = {}
    for item_id, ticker, kind, symbol, expiry, taken_at in db.recent_option_snapshots(conn, now - timedelta(days=10)):
        g = groups.setdefault((item_id, ticker), {"kinds": set(), "entry": {}, "entry_day": None})
        g["kinds"].add(kind)
        if kind == "entry":
            g["entry"][symbol] = expiry
            g["entry_day"] = taken_at.astimezone(NY).date()

    due = []  # (item_id, ticker, kind, {symbol: expiry})
    for (item_id, ticker), g in groups.items():
        if g["entry_day"] not in index:
            continue
        kind = FOLLOW_UPS.get(index[today] - index[g["entry_day"]])
        if kind and kind not in g["kinds"]:
            due.append((item_id, ticker, kind, g["entry"]))
    if not due:
        return {"due": 0}

    needed = sorted({(t, e) for _, t, _, contracts in due for e in contracts.values() if e >= today})

    def one(key):
        try:
            return key, source.chain(*key)
        except Exception as e:
            log.warning("option chain failed for %s %s: %s", key[0], key[1], e)
            return key, None

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        chains = {k: c for k, c in pool.map(one, needed) if c is not None and is_live(c, now)}

    rows: List[dict] = []
    for item_id, ticker, kind, contracts in due:
        for expiry in sorted(set(contracts.values())):
            chain = chains.get((ticker, expiry))
            if chain is None:
                continue  # expired, or the fetch failed
            wanted = [q for q in chain.quotes if contracts.get(q.contract_symbol) == expiry]
            rows += snapshot_rows(item_id, ticker, kind, chain, wanted, now)
    db.insert_option_snapshots(conn, rows)
    return {"due": len(due), "contracts": len(rows), "chain_failures": len(needed) - len(chains)}
