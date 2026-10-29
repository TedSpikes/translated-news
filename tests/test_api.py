from datetime import UTC, datetime

from fastapi.testclient import TestClient

from translated_news import database
from translated_news.feeds import Article
from translated_news.main import create_app


def make_article(guid: str, published_at: datetime, headline: str = "כותרת") -> Article:
    return Article(
        feed_name="ynet",
        guid=guid,
        url=f"https://www.ynet.co.il/news/article/{guid}",
        headline=headline,
        summary="תקציר.",
        published_at=published_at,
    )


def test_articles_come_back_newest_first(tmp_path):
    database_path = tmp_path / "articles.db"
    app = create_app(database_path=database_path, refresh_interval_seconds=600, refresh_feeds_in_background=False)
    older = make_article("older", datetime(2026, 10, 29, 10, 0, tzinfo=UTC))
    newer = make_article("newer", datetime(2026, 10, 29, 11, 0, tzinfo=UTC))

    with TestClient(app) as client:
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
        }
    ]


def test_saving_an_article_again_updates_its_headline(tmp_path):
    database_path = tmp_path / "articles.db"
    database.create_tables(database_path)
    published_at = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)

    database.save_articles(database_path, [make_article("same", published_at, headline="ישנה")])
    database.save_articles(database_path, [make_article("same", published_at, headline="חדשה")])

    [article] = database.list_newest_articles(database_path, limit=10)
    assert article.headline == "חדשה"
