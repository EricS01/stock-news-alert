from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import timedelta
from typing import Dict, List, Optional

from . import db
from .config import load_config
from .matcher import Matcher
from .models import Alert, NewsItem
from .notifier import Notifier
from .scorer import MODEL, PROMPT_VERSION, SYSTEM, ScoreResult, Scorer
from .sources import hackernews, news_feeds, reddit, sec_edgar

log = logging.getLogger("alerts")

JOB = "alerts"
# Re-fetch a little before the last good run so items published while it ran aren't missed;
# the database dedupes the overlap. Never look back further than MAX_LOOKBACK.
OVERLAP = timedelta(minutes=30)
MAX_LOOKBACK = timedelta(hours=24)


class RunFailed(RuntimeError):
    pass


def collect(cfg, since: Optional[float]) -> List[NewsItem]:
    sources = {
        "hackernews": lambda: hackernews.fetch(since=since),
        "news": lambda: news_feeds.fetch(cfg.stocks, since=since),
        "sec": lambda: sec_edgar.fetch(list(cfg.stocks), since=since),
        "reddit": lambda: reddit.fetch(since=since),
    }
    if not (os.environ.get("REDDIT_CLIENT_ID") and os.environ.get("REDDIT_CLIENT_SECRET")):
        log.info("reddit: skipped (REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET not set)")
        sources.pop("reddit")
    items: List[NewsItem] = []
    for name, fetch in sources.items():
        if not cfg.source_enabled(name):
            continue
        try:
            got = fetch()
            log.info("%s: %d items", name, len(got))
            items += got
        except Exception as e:  # one broken source shouldn't stop the run
            log.warning("%s failed: %s", name, e)
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


def process(conn, items: List[NewsItem], matcher: Matcher, notifier: Notifier, scorer, threshold: int, dry_run: bool = False) -> dict:
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

    result: ScoreResult = scorer(pairs)
    if result.all_failed:
        raise RunFailed(f"all {result.batches} scoring batches failed")

    sent: Dict[tuple, bool] = {}
    notify_failures = 0
    for alert in result.alerts:
        floor = sec_edgar.score_floor(alert.item)
        alert.floor_applied = floor > alert.score
        alert.score = max(alert.score, floor)
        log.info("%s %d/5 %s", alert.ticker, alert.score, alert.item.title)
        if alert.score < threshold:
            continue
        try:
            notifier.send(alert)
            sent[(alert.item.id, alert.ticker)] = True
        except Exception as e:  # keep going so the rest are still sent and recorded
            log.error("push failed for %s %s: %s", alert.ticker, alert.item.id, e)
            notify_failures += 1

    failed_ids = {item.id for item, _ in result.failed}
    if conn is not None and not dry_run:
        alerts_by_item: Dict[str, List[Alert]] = {}
        for alert in result.alerts:
            alerts_by_item.setdefault(alert.item.id, []).append(alert)
        for item in items:
            if item.id in failed_ids:
                continue
            with conn.transaction():
                db.insert_item(conn, item)
                for a in alerts_by_item.get(item.id, []):
                    db.insert_score(
                        conn,
                        item_id=item.id,
                        ticker=a.ticker,
                        prompt_version=PROMPT_VERSION,
                        score=a.score,
                        direction=a.direction,
                        reason=a.reason,
                        floor_applied=a.floor_applied,
                        alerted=sent.get((item.id, a.ticker), False),
                    )

    counts = {
        "fetched": fetched,
        "new_items": len(items),
        "pairs": len(pairs),
        "scored": len(result.alerts),
        "failed_pairs": len(result.failed),
        "alerts": len(sent),
        "notify_failures": notify_failures,
    }
    if notify_failures:
        raise RunFailed(f"{notify_failures} push notification(s) failed", counts)
    return counts


def run(dry_run: bool = False) -> None:
    cfg = load_config()
    matcher = Matcher(cfg.stocks)
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
            process(conn, items, matcher, notifier, scorer, threshold, dry_run=True)
        else:
            record_run(conn, lambda: process(conn, collect(cfg, fetch_since(conn)), matcher, notifier, scorer, threshold))
    finally:
        if conn is not None:
            conn.close()


def record_run(conn, work) -> dict:
    """Run `work` inside a `runs` row: ok with its counts, or failed with the error."""
    started = time.monotonic()
    db.ensure_prompt_version(conn, PROMPT_VERSION, MODEL, SYSTEM)
    run_id = db.start_run(conn, JOB)
    try:
        counts = work()
    except Exception as e:
        counts = e.args[1] if isinstance(e, RunFailed) and len(e.args) > 1 else {}
        counts["duration_s"] = round(time.monotonic() - started, 1)
        db.finish_run(conn, run_id, "failed", counts, error=str(e.args[0]) if e.args else repr(e))
        raise
    counts["duration_s"] = round(time.monotonic() - started, 1)
    log.info("run counts: %s", counts)
    db.finish_run(conn, run_id, "ok", counts)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check news sources and push stock alerts")
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of pushing; don't write to the database")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(dry_run=args.dry_run)
