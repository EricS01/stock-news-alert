from __future__ import annotations

import os
from typing import Optional

import requests

from .models import Alert

NTFY_SERVER = os.environ.get("NTFY_SERVER", "https://ntfy.sh")


def format_alert(alert: Alert) -> dict:
    arrow = {"bullish": "▲", "bearish": "▼"}.get(alert.direction, "•")
    return {
        "title": f"{alert.ticker} {arrow} {alert.score}/5 — {alert.item.title}"[:200],
        "message": f"{alert.reason}\n\nvia {alert.item.source}",
        "click": alert.item.url,
    }


class Notifier:
    def __init__(self, topic: Optional[str] = None, dry_run: bool = False):
        self.topic = topic or os.environ.get("NTFY_TOPIC")
        self.dry_run = dry_run
        if not self.topic and not dry_run:
            raise RuntimeError("NTFY_TOPIC is not set")

    def send(self, alert: Alert) -> None:
        payload = format_alert(alert)
        if self.dry_run:
            print(f"[dry-run] {payload['title']}\n          {payload['message'].splitlines()[0]}\n          {payload['click']}")
            return
        # JSON publishing avoids HTTP-header encoding issues with unicode titles.
        resp = requests.post(NTFY_SERVER, json={"topic": self.topic, **payload}, timeout=15)
        resp.raise_for_status()
