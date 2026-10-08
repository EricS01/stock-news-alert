from __future__ import annotations

import argparse
import logging
from typing import List

from .config import load_config
from .matcher import Matcher
from .models import Alert, NewsItem
from .notifier import Notifier
from .sources import hackernews
from .state import State

log = logging.getLogger("alerts")


def collect(cfg, state: State) -> List[NewsItem]:
    items: List[NewsItem] = []
    if cfg.source_enabled("hackernews"):
        try:
            items += hackernews.fetch(since=state.last_run)
        except Exception as e:  # one broken source shouldn't stop the run
            log.warning("hackernews failed: %s", e)
    return items


def run(dry_run: bool = False) -> None:
    cfg = load_config()
    state = State()
    matcher = Matcher(cfg.stocks)
    notifier = Notifier(dry_run=dry_run)

    items = [i for i in collect(cfg, state) if not state.is_seen(i.id)]
    log.info("fetched %d new items", len(items))

    for item in items:
        for ticker in matcher.match(item):
            notifier.send(Alert(item=item, ticker=ticker, score=3, direction="neutral", reason="Mentioned"))
        state.mark_seen(item.id)
    if not dry_run:  # dry runs shouldn't consume items
        state.save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check news sources and push stock alerts")
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of pushing; don't save state")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run(dry_run=args.dry_run)
