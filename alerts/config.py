from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PATH = ROOT / "watchlist.yaml"


@dataclass
class Config:
    stocks: Dict[str, List[str]]
    threshold: int = 4
    sources: Dict[str, bool] = field(default_factory=dict)

    def source_enabled(self, name: str) -> bool:
        return self.sources.get(name, True)


def load_config(path: Path = DEFAULT_PATH) -> Config:
    data = yaml.safe_load(Path(path).read_text()) or {}
    stocks = {t.upper(): list(aliases or []) for t, aliases in (data.get("stocks") or {}).items()}
    if not stocks:
        raise ValueError(f"No stocks defined in {path}")
    return Config(
        stocks=stocks,
        threshold=int(data.get("threshold", 4)),
        sources=data.get("sources") or {},
    )
