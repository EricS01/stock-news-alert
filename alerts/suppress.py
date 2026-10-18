"""Decide which qualifying alerts are worth a push.

One story is usually syndicated many times (Yahoo and Google list the same
headline, publishers rewrite it), and one event produces many stories. Pushing
all of them buried the useful alerts. Suppressed alerts are still scored and
recorded, with the reason, so the research data stays complete.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, Optional, Set, Tuple

from .models import Alert

# Google News appends " - Publisher" to every headline.
PUBLISHER_SUFFIX = re.compile(r"\s+[-–—|]\s+[^-–—|]{2,60}$")


def story_key(title: str) -> str:
    """Normalized headline: no publisher suffix, case, or punctuation."""
    return " ".join(re.findall(r"[a-z0-9]+", PUBLISHER_SUFFIX.sub("", title).lower()))


class Suppressor:
    """Push a story once per ticker, and at most one alert per ticker and direction
    per cooldown window unless a later one scores higher."""

    def __init__(self, recent: Iterable[Tuple[str, str, int, str]] = ()):
        """`recent`: (ticker, title, score, direction) of alerts pushed within the cooldown."""
        self.stories: Set[Tuple[str, str]] = set()
        self.best: Dict[Tuple[str, str], int] = {}
        for ticker, title, score, direction in recent:
            self._remember(ticker, title, score, direction)

    def _remember(self, ticker: str, title: str, score: int, direction: str) -> None:
        self.stories.add((ticker, story_key(title)))
        self.best[(ticker, direction)] = max(score, self.best.get((ticker, direction), 0))

    def reason(self, alert: Alert) -> Optional[str]:
        """Why this alert shouldn't be pushed, or None to push it."""
        if (alert.ticker, story_key(alert.item.title)) in self.stories:
            return "duplicate"
        previous = self.best.get((alert.ticker, alert.direction))
        if previous is not None and alert.score <= previous:
            return "cooldown"
        return None

    def pushed(self, alert: Alert) -> None:
        self._remember(alert.ticker, alert.item.title, alert.score, alert.direction)
