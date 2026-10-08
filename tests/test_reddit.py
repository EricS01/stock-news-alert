from types import SimpleNamespace

from alerts.sources.reddit import parse_submissions


def _post(id, created, title="Tesla deliveries miss", stickied=False):
    return SimpleNamespace(id=id, created_utc=created, title=title, selftext="dd inside", permalink=f"/r/stocks/comments/{id}/x/",
                           subreddit="stocks", score=10, url="https://x.test", stickied=stickied)


def test_parse_submissions():
    items = parse_submissions([_post("a", 200), _post("b", 50), _post("c", 300, stickied=True)], since=100)
    assert [i.id for i in items] == ["reddit:a"]
    assert items[0].url == "https://www.reddit.com/r/stocks/comments/a/x/"
