from __future__ import annotations

import argparse
import logging
import os
from typing import List

from .config import load_config
from .matcher import Matcher
from .models import Alert, NewsItem
from .notifier import Notifier
from .scorer import Scorer
from .sources import hackernews, news_feeds, sec_edgar
from .state import State

log = logging.getLogger("alerts")


def collect(cfg, state: State) -> List[NewsItem]:
    sources = {
        "hackernews": lambda: hackernews.fetch(since=state.last_run),
        "news": lambda: news_feeds.fetch(cfg.stocks, since=state.last_run),
        "sec": lambda: sec_edgar.fetch(list(cfg.stocks), since=state.last_run),
    }
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


def run(dry_run: bool = False) -> None:
    cfg = load_config()
    state = State()
    matcher = Matcher(cfg.stocks)
    notifier = Notifier(dry_run=dry_run)

    items = [i for i in collect(cfg, state) if not state.is_seen(i.id)]
    pairs = [(item, ticker) for item in items for ticker in matcher.match(item)]
    log.info("fetched %d new items, %d watchlist matches", len(items), len(pairs))

    if os.environ.get("ANTHROPIC_API_KEY"):
        alerts = Scorer().score(pairs)
        threshold = cfg.threshold
    elif dry_run:
        log.warning("ANTHROPIC_API_KEY not set; showing unscored matches")
        alerts = [Alert(item=i, ticker=t, score=0, direction="neutral", reason="(unscored)") for i, t in pairs]
        threshold = 0
    else:
        raise RuntimeError("ANTHROPIC_API_KEY is not set")

    for alert in alerts:
        alert.score = max(alert.score, sec_edgar.score_floor(alert.item))
        log.info("%s %d/5 %s", alert.ticker, alert.score, alert.item.title)
        if alert.score >= threshold:
            notifier.send(alert)

    for item in items:
        state.mark_seen(item.id)
    if not dry_run:  # dry runs shouldn't consume items
        state.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check news sources and push stock alerts")
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of pushing; don't save state")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(dry_run=args.dry_run)
