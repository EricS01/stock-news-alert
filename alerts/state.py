from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, Optional

from .config import ROOT

DEFAULT_PATH = ROOT / "state" / "seen.json"
RETENTION_SECONDS = 7 * 24 * 3600


class State:
    """Remembers which items were already processed so we never alert twice."""

    def __init__(self, path: Path = DEFAULT_PATH):
        self.path = Path(path)
        self.seen: Dict[str, float] = {}
        self.last_run: Optional[float] = None
        if self.path.exists():
            data = json.loads(self.path.read_text() or "{}")
            self.seen = data.get("seen", {})
            self.last_run = data.get("last_run")

    def is_seen(self, item_id: str) -> bool:
        return item_id in self.seen

    def mark_seen(self, item_id: str) -> None:
        self.seen[item_id] = time.time()

    def save(self) -> None:
        cutoff = time.time() - RETENTION_SECONDS
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        self.last_run = time.time()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"last_run": self.last_run, "seen": self.seen}, indent=1, sort_keys=True) + "\n"
        )
