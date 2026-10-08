import time

from alerts.config import load_config
from alerts.state import RETENTION_SECONDS, State


def test_state_roundtrip(tmp_path):
    path = tmp_path / "seen.json"
    s = State(path)
    assert not s.is_seen("hn:1")
    s.mark_seen("hn:1")
    s.save()

    s2 = State(path)
    assert s2.is_seen("hn:1")
    assert s2.last_run is not None


def test_state_prunes_old_entries(tmp_path):
    path = tmp_path / "seen.json"
    s = State(path)
    s.seen["old"] = time.time() - RETENTION_SECONDS - 10
    s.mark_seen("new")
    s.save()
    s2 = State(path)
    assert not s2.is_seen("old")
    assert s2.is_seen("new")


def test_default_watchlist_loads():
    cfg = load_config()
    assert "NVDA" in cfg.stocks
    assert cfg.threshold == 4
