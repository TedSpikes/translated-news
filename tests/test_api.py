import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from translated_news import database, refresh
from translated_news.feeds import Article
from translated_news.main import create_app


def make_article(guid: str, published_at: datetime, feed_name: str = "ynet", headline: str = "כותרת") -> Article:
    return Article(
        feed_name=feed_name,
        guid=guid,
        url=f"https://www.ynet.co.il/news/article/{guid}",
        headline=headline,
        summary="תקציר.",
        published_at=published_at,
    )


@pytest.fixture
def database_path(tmp_path):
    return tmp_path / "articles.db"


@pytest.fixture
def client(database_path):
    with TestClient(create_app(database_path)) as client:
        yield client


def test_refresh_saves_the_feeds_and_a_second_refresh_within_a_minute_is_skipped(client, monkeypatch):
    fetch_count = 0

    async def fake_fetch_all_feeds():
        nonlocal fetch_count
        fetch_count += 1
        return [make_article("fresh", datetime.now(UTC))]

    monkeypatch.setattr(refresh, "fetch_all_feeds", fake_fetch_all_feeds)

    first = client.post("/refresh").json()
    second = client.post("/refresh").json()

    assert first == {"refreshed": True, "articles_fetched": 1}
    assert second == {"refreshed": False, "articles_fetched": 0}
    assert fetch_count == 1
    assert [article["guid"] for article in client.get("/articles").json()] == ["fresh"]


def test_refresh_deletes_articles_older_than_a_year(database_path, monkeypatch):
    database.create_tables(database_path)
    now = datetime.now(UTC)
    database.save_articles(
        database_path,
        [make_article("too-old", now - timedelta(days=366)), make_article("recent", now - timedelta(days=364))],
    )

    async def no_articles():
        return []

    monkeypatch.setattr(refresh, "fetch_all_feeds", no_articles)
    asyncio.run(refresh.refresh_feeds(database_path))

    assert [article.guid for article in database.list_newest_articles(database_path, 10, ["ynet"])] == ["recent"]


def test_articles_come_back_newest_first_with_their_site(client, database_path):
    older = make_article("older", datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    newer = make_article("newer", datetime(2026, 10, 29, 11, 0, tzinfo=UTC))
    database.save_articles(database_path, [older, newer])

    response = client.get("/articles", params={"limit": 1})

    assert response.status_code == 200
    assert response.json() == [
        {
            "feed_name": "ynet",
            "guid": "newer",
            "url": "https://www.ynet.co.il/news/article/newer",
            "headline": "כותרת",
            "summary": "תקציר.",
            "published_at": "2026-10-29T11:00:00Z",
            "site": {"name": "Ynet", "favicon_url": "https://www.ynet.co.il/images/favicon/favicon_1.ico"},
        }
    ]


def test_category_filter_returns_only_that_categorys_feeds(client, database_path):
    published_at = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    database.save_articles(
        database_path,
        [
            make_article("politics-story", published_at, feed_name="ynet"),
            make_article("finance-story", published_at, feed_name="globes"),
            make_article("culture-story", published_at, feed_name="ynet-culture"),
        ],
    )

    response = client.get("/articles", params={"category": "finance"})

    assert [article["guid"] for article in response.json()] == ["finance-story"]


def test_unknown_category_is_rejected(client):
    assert client.get("/articles", params={"category": "sport"}).status_code == 422


def test_paging_with_older_than_returns_every_article_once_even_with_equal_times(client, database_path):
    same_minute = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    database.save_articles(
        database_path,
        [
            make_article("newest", datetime(2026, 10, 29, 11, 0, tzinfo=UTC), feed_name="globes"),
            make_article("tie-a", same_minute, feed_name="globes"),
            make_article("tie-b", same_minute, feed_name="globes"),
            make_article("tie-c", same_minute, feed_name="themarker"),
            make_article("oldest", datetime(2026, 10, 29, 9, 0, tzinfo=UTC), feed_name="ynet-economy"),
        ],
    )

    seen_guids, older_than = [], {}
    while True:
        page = client.get("/articles", params={"category": "finance", "limit": 2, **older_than}).json()
        seen_guids += [article["guid"] for article in page]
        if len(page) < 2:
            break
        last = page[-1]
        older_than = {
            "older_than_published_at": last["published_at"],
            "older_than_feed_name": last["feed_name"],
            "older_than_guid": last["guid"],
        }

    assert seen_guids == ["newest", "tie-c", "tie-b", "tie-a", "oldest"]


def test_older_than_needs_all_three_parts(client):
    response = client.get("/articles", params={"older_than_published_at": "2026-10-29T10:00:00Z"})
    assert response.status_code == 422


def test_page_is_served_at_the_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "<title>Translated News</title>" in response.text


def test_saving_an_article_again_updates_its_headline(database_path):
    database.create_tables(database_path)
    published_at = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)

    database.save_articles(database_path, [make_article("same", published_at, headline="ישנה")])
    database.save_articles(database_path, [make_article("same", published_at, headline="חדשה")])

    [article] = database.list_newest_articles(database_path, limit=10, feed_names=["ynet"])
    assert article.headline == "חדשה"
