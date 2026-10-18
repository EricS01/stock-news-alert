from __future__ import annotations

import argparse
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from . import db
from .config import load_config
from .matcher import Matcher
from .models import Alert, NewsItem
from .notifier import Notifier
from .options import snapshots
from .options.yahoo import YahooChains
from .runs import RunFailed, record_run
from .scorer import MODEL, PROMPT_VERSION, SYSTEM, ScoreResult, Scorer
from .sources import hackernews, news_feeds, reddit, sec_edgar
from .suppress import Suppressor

log = logging.getLogger("alerts")

JOB = "alerts"
# Re-fetch a little before the last good run so items published while it ran aren't missed;
# the database dedupes the overlap. Never look back further than MAX_LOOKBACK.
OVERLAP = timedelta(minutes=30)
MAX_LOOKBACK = timedelta(hours=24)


def collect(cfg, since: Optional[float], timings: Optional[Dict[str, float]] = None) -> List[NewsItem]:
    """Fetch every enabled source concurrently. Per-source seconds go into `timings`."""
    sources = {
        "hackernews": lambda: hackernews.fetch(since=since),
        "news": lambda: news_feeds.fetch(cfg.stocks, since=since),
        "sec": lambda: sec_edgar.fetch(list(cfg.stocks), since=since),
        "reddit": lambda: reddit.fetch(since=since),
    }
    if not (os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET")):
        log.info("reddit: skipped (REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET not set)")
        sources.pop("reddit")
    sources = {name: fetch for name, fetch in sources.items() if cfg.source_enabled(name)}
    timings = timings if timings is not None else {}

    def timed(name: str) -> List[NewsItem]:
        started = time.monotonic()
        try:
            got = sources[name]()
            log.info("%s: %d items", name, len(got))
            return got
        except Exception as e:  # one broken source shouldn't stop the run
            log.warning("%s failed: %s", name, e)
            return []
        finally:
            timings[name] = round(time.monotonic() - started, 1)

    items: List[NewsItem] = []
    with ThreadPoolExecutor(max_workers=max(len(sources), 1)) as pool:
        for got in pool.map(timed, sources):
            items += got
    return items


def dedupe(items: List[NewsItem]) -> List[NewsItem]:
    """Collapse items fetched more than once in a run (e.g. from two per-ticker feeds)."""
    by_id: Dict[str, NewsItem] = {}
    for item in items:
        if item.id in by_id:
            kept = by_id[item.id]
            kept.tickers += [t for t in item.tickers if t not in kept.tickers]
        else:
            by_id[item.id] = item
    return list(by_id.values())


def fetch_since(conn) -> Optional[float]:
    if conn is None:
        return None  # sources fall back to their default lookback
    last = db.last_ok_run_start(conn, JOB)
    if last is None:
        return None
    floor = time.time() - MAX_LOOKBACK.total_seconds()
    return max((last - OVERLAP).timestamp(), floor)


def score_pairs(pairs, dry_run: bool) -> ScoreResult:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return Scorer().score(pairs)
    if dry_run:
        log.warning("ANTHROPIC_API_KEY not set; showing unscored matches")
        return ScoreResult(alerts=[Alert(item=i, ticker=t, score=0, direction="neutral", reason="(unscored)") for i, t in pairs])
    raise RunFailed("ANTHROPIC_API_KEY is not set")


def process(
    conn,
    items: List[NewsItem],
    matcher: Matcher,
    notifier: Notifier,
    scorer,
    threshold: int,
    dry_run: bool = False,
    cooldown: timedelta = timedelta(hours=12),
) -> dict:
    """Score new items, push alerts, and record items and scores. Returns run counts.

    An item is only written to news_items (and so counts as processed) once all of
    its pairs were scored; items whose scoring failed are picked up by a later run.
    """
    fetched = len(items)
    items = dedupe(items)
    if conn is not None:
        seen = db.existing_item_ids(conn, (i.id for i in items))
        items = [i for i in items if i.id not in seen]
    pairs = [(item, ticker) for item in items for ticker in matcher.match(item)]
    log.info("fetched %d new items, %d watchlist matches", len(items), len(pairs))

    t = time.monotonic()
    result: ScoreResult = scorer(pairs)
    score_s = round(time.monotonic() - t, 1)
    if result.all_failed:
        raise RunFailed(f"all {result.batches} scoring batches failed")

    for alert in result.alerts:
        floor = sec_edgar.score_floor(alert.item)
        alert.floor_applied = floor > alert.score
        alert.score = max(alert.score, floor)
        log.info("%s %d/5 %s", alert.ticker, alert.score, alert.item.title)

    recent = db.recent_alerts(conn, datetime.now(timezone.utc) - cooldown) if conn is not None else []
    suppressor = Suppressor(recent)
    sent: Dict[tuple, bool] = {}
    suppressed: Dict[tuple, str] = {}
    notify_failures = 0
    # Highest scores first, so the strongest version of a story is the one pushed.
    for alert in sorted((a for a in result.alerts if a.score >= threshold), key=lambda a: -a.score):
        key = (alert.item.id, alert.ticker)
        reason = suppressor.reason(alert)
        if reason:
            suppressed[key] = reason
            log.info("not pushed (%s): %s %s", reason, alert.ticker, alert.item.title)
            continue
        try:
            notifier.send(alert)
            sent[key] = True
            suppressor.pushed(alert)
        except Exception as e:  # keep going so the rest are still sent and recorded
            log.error("push failed for %s %s: %s", alert.ticker, alert.item.id, e)
            notify_failures += 1

    failed_ids = {item.id for item, _ in result.failed}
    if conn is not None and not dry_run:
        done = [item for item in items if item.id not in failed_ids]
        scores = [
            {
                "item_id": a.item.id,
                "ticker": a.ticker,
                "prompt_version": PROMPT_VERSION,
                "score": a.score,
                "direction": a.direction,
                "reason": a.reason,
                "floor_applied": a.floor_applied,
                "alerted": sent.get((a.item.id, a.ticker), False),
                "suppressed": suppressed.get((a.item.id, a.ticker)),
            }
            for a in result.alerts
        ]
        t = time.monotonic()
        snapshot = list(sent)  # only pushed alerts: suppressed ones would repeat the same quotes
        with conn.transaction():  # all or nothing: a failed write leaves every item to retry
            db.insert_items(conn, done)
            db.insert_scores(conn, scores)
            db.enqueue_snapshots(conn, snapshot)  # options entry snapshot, taken during market hours
        write_s = round(time.monotonic() - t, 1)
    else:
        write_s = 0.0

    counts = {
        "fetched": fetched,
        "new_items": len(items),
        "pairs": len(pairs),
        "scored": len(result.alerts),
        "failed_pairs": len(result.failed),
        "alerts": len(sent),
        "suppressed_duplicate": sum(r == "duplicate" for r in suppressed.values()),
        "suppressed_cooldown": sum(r == "cooldown" for r in suppressed.values()),
        "notify_failures": notify_failures,
        "score_s": score_s,
        "write_s": write_s,
    }
    if notify_failures:
        raise RunFailed(f"{notify_failures} push notification(s) failed", counts)
    return counts


def run(dry_run: bool = False) -> None:
    cfg = load_config()
    matcher = Matcher(cfg.stocks, cfg.cashtag_only)
    notifier = Notifier(dry_run=dry_run)
    # Unscored dry runs (no API key) show every match.
    threshold = cfg.threshold if os.environ.get("ANTHROPIC_API_KEY") else 0
    scorer = lambda pairs: score_pairs(pairs, dry_run)  # noqa: E731

    if os.environ.get("DATABASE_URL"):
        conn = db.connect()
    elif dry_run:
        log.warning("DATABASE_URL not set; dry run without dedupe")
        conn = None
    else:
        raise RunFailed("DATABASE_URL is not set")

    try:
        if dry_run:  # dry runs read the database for dedupe but never write
            items = collect(cfg, fetch_since(conn))
            process(conn, items, matcher, notifier, scorer, threshold, dry_run=True,
                    cooldown=timedelta(hours=cfg.alert_cooldown_hours))
        else:
            def work() -> dict:
                db.ensure_prompt_version(conn, PROMPT_VERSION, MODEL, SYSTEM)
                fetch_s: Dict[str, float] = {}
                items = collect(cfg, fetch_since(conn), fetch_s)
                counts = process(conn, items, matcher, notifier, scorer, threshold,
                                 cooldown=timedelta(hours=cfg.alert_cooldown_hours))
                counts["fetch_s"] = fetch_s
                t = time.monotonic()
                counts["options"] = snapshots.guarded("entries", snapshots.take_entries, conn, YahooChains(), datetime.now(timezone.utc))
                counts["options_s"] = round(time.monotonic() - t, 1)
                return counts

            record_run(conn, JOB, work)
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check news sources and push stock alerts")
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of pushing; don't write to the database")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(dry_run=args.dry_run)
