from alerts.models import Alert, NewsItem
from alerts.notifier import Notifier, format_alert


def _alert(score=4, direction="bearish"):
    item = NewsItem(id="hn:1", source="hackernews", title="Tesla recalls 2M cars", url="https://x.test/a")
    return Alert(item=item, ticker="TSLA", score=score, direction=direction, reason="Large recall")


def test_format_alert():
    p = format_alert(_alert())
    assert p["title"].startswith("TSLA ▼ 4/5")
    assert p["click"] == "https://x.test/a"
    assert p["priority"] == 4
    assert p["tags"] == ["chart_with_downwards_trend"]
    assert format_alert(_alert(score=5))["priority"] == 5


def test_dry_run_does_not_need_topic(monkeypatch, capsys):
    monkeypatch.delenv("NTFY_TOPIC", raising=False)
    Notifier(dry_run=True).send(_alert())
    assert "[dry-run] TSLA" in capsys.readouterr().out
