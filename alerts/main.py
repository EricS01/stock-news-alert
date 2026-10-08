from __future__ import annotations

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


def run() -> None:
    cfg = load_config()
    state = State()
    matcher = Matcher(cfg.stocks)
    notifier = Notifier()

    items = [i for i in collect(cfg, state) if not state.is_seen(i.id)]
    log.info("fetched %d new items", len(items))

    for item in items:
        for ticker in matcher.match(item):
            notifier.send(Alert(item=item, ticker=ticker, score=3, direction="neutral", reason="Mentioned"))
        state.mark_seen(item.id)
    state.save()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run()
