import pytest

from alerts.matcher import Matcher

STOCKS = {
    "NVDA": ["Nvidia"],
    "META": ["Meta Platforms", "Facebook"],
    "AAPL": ["Apple"],
    "TSLA": ["Tesla"],
}


@pytest.fixture
def m():
    return Matcher(STOCKS)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Nvidia beats earnings estimates", ["NVDA"]),
        ("NVIDIA and Tesla team up", ["NVDA", "TSLA"]),
        ("$NVDA is up 5% premarket", ["NVDA"]),
        ("Why I'm buying TSLA", ["TSLA"]),
        ("Facebook outage hits millions", ["META"]),
        ("Apple's new iPhone", ["AAPL"]),
    ],
)
def test_matches(m, text, expected):
    assert m.match_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "A meta-analysis of sleep studies",  # lowercase 'meta' is not the ticker
        "Metadata is all you need",
        "Pineapple pizza debate",  # alias must be a whole word
        "Nvidiaish GPUs",
    ],
)
def test_no_false_positives(m, text):
    assert m.match_text(text) == []
