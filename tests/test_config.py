from alerts.config import load_config


def test_default_watchlist_loads():
    cfg = load_config()
    assert "NVDA" in cfg.stocks
    assert cfg.threshold == 4


def test_watchlist_has_known_ciks():
    from alerts.sources.sec_edgar import KNOWN_CIKS

    cfg = load_config()
    assert len(cfg.stocks) == 17
    assert set(cfg.stocks) <= set(KNOWN_CIKS)
    assert cfg.cashtag_only <= set(cfg.stocks)
