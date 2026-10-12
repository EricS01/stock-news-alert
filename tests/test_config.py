from alerts.config import load_config


def test_default_watchlist_loads():
    cfg = load_config()
    assert "NVDA" in cfg.stocks
    assert cfg.threshold == 4
