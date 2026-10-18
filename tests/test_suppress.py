from alerts.models import Alert, NewsItem
from alerts.suppress import Suppressor, story_key


def _alert(title, ticker="TSLA", score=4, direction="bullish", id="n:1"):
    return Alert(item=NewsItem(id=id, source="news", title=title, url="u"), ticker=ticker,
                 score=score, direction=direction, reason="")


def test_story_key_ignores_publisher_suffix_case_and_punctuation():
    a = "Tesla-SpaceX Merger could be announced before December, Barclays says - Investing.com"
    b = "Tesla-SpaceX merger could be announced before December, Barclays says"
    assert story_key(a) == story_key(b) == "tesla spacex merger could be announced before december barclays says"


def test_duplicate_story_is_suppressed_for_the_same_ticker_only():
    s = Suppressor()
    first = _alert("Tesla recalls 2M cars - Reuters")
    assert s.reason(first) is None
    s.pushed(first)
    assert s.reason(_alert("Tesla recalls 2M cars - CNBC", score=5)) == "duplicate"
    assert s.reason(_alert("Tesla recalls 2M cars", ticker="NVDA")) is None


def test_cooldown_lets_through_a_higher_score_or_the_other_direction():
    s = Suppressor([("UNH", "UnitedHealth beats on earnings", 4, "bullish")])
    assert s.reason(_alert("UNH raises guidance", ticker="UNH", score=4)) == "cooldown"
    assert s.reason(_alert("UNH raises guidance", ticker="UNH", score=5)) is None
    assert s.reason(_alert("DOJ probes UNH", ticker="UNH", score=4, direction="bearish")) is None
