import asyncio
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from translated_news import database, main, refresh
from translated_news.feeds import Article
from translated_news.main import create_app
from translated_news.translate import TranslationOutcome


def make_article(guid: str, published_at: datetime, feed_name: str = "ynet", headline: str = "כותרת") -> Article:
    return Article(
        feed_name=feed_name,
        guid=guid,
        url=f"https://www.ynet.co.il/news/article/{guid}",
        headline=headline,
        summary="תקציר.",
        published_at=published_at,
    )


def translation_of(article: Article, english_headline: str) -> database.ArticleTranslation:
    return database.ArticleTranslation(
        feed_name=article.feed_name,
        guid=article.guid,
        source_headline=article.headline,
        source_summary=article.summary,
        english_headline=english_headline,
        english_summary="English summary.",
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
            "english_headline": None,
            "english_summary": None,
            "site": {"name": "Ynet", "favicon_url": "https://www.ynet.co.il/images/favicon/favicon_1.ico"},
        }
    ]


def streamed_lines(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line]


def test_translate_sends_each_untranslated_article_of_the_newest_20_per_category_separately(client, database_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    now = datetime.now(UTC)
    politics = [make_article(f"politics-{i:02}", now - timedelta(minutes=i), feed_name="ynet") for i in range(25)]
    finance = [make_article("finance-0", now, feed_name="globes")]
    database.save_articles(database_path, politics + finance)
    database.save_translations(database_path, [translation_of(politics[0], "Already translated")])
    requested = []

    async def fake_translate_article(article):
        requested.append(article.guid)
        return TranslationOutcome(
            translation=translation_of(article, f"English {article.guid}"), failure=None, input_tokens=1000, output_tokens=200
        )

    monkeypatch.setattr(main, "translate_article", fake_translate_article)

    response = client.post("/translate")

    assert response.status_code == 200
    # Politics' newest 20 minus the one already translated, plus finance; articles 21-25 are left alone.
    expected = {"finance-0"} | {f"politics-{i:02}" for i in range(1, 20)}
    assert sorted(requested) == sorted(expected)
    assert {line["translation"]["guid"] for line in streamed_lines(response)} == expected
    newest_politics = client.get("/articles", params={"category": "politics", "limit": 2}).json()
    assert [article["english_headline"] for article in newest_politics] == ["Already translated", "English politics-01"]
    this_month = client.get("/usage").json()["this_month"]
    assert this_month["input_tokens"] == 20 * 1000
    assert this_month["articles_translated"] == 21  # 20 from /translate, plus the one saved directly above

    assert streamed_lines(client.post("/translate")) == []  # nothing left among those 20
    assert len(requested) == 20


def test_a_failed_article_is_reported_its_tokens_counted_and_it_stays_untranslated(client, database_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    database.save_articles(database_path, [make_article("a", datetime.now(UTC))])

    async def declining_translate_article(article):
        return TranslationOutcome(translation=None, failure="Claude declined it", input_tokens=900, output_tokens=5)

    monkeypatch.setattr(main, "translate_article", declining_translate_article)

    response = client.post("/translate")

    assert streamed_lines(response) == [{"failure": "Claude declined it"}]
    assert client.get("/usage").json()["this_month"]["input_tokens"] == 900
    assert client.get("/articles").json()[0]["english_headline"] is None


def test_translate_without_an_api_key_answers_502_before_calling_claude(client, database_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    database.save_articles(database_path, [make_article("a", datetime.now(UTC))])

    response = client.post("/translate")

    assert response.status_code == 502
    assert "ANTHROPIC_API_KEY" in response.json()["detail"]


def test_usage_counts_articles_in_the_database_and_spending_by_month(client, database_path):
    now = datetime.now(UTC)
    long_ago = datetime(2000, 1, 15, tzinfo=UTC)
    database.save_articles(database_path, [make_article("a", now), make_article("b", now), make_article("old", long_ago)])
    database.save_articles(database_path, [make_article("b", now), make_article("c", now)])  # "b" again isn't counted twice
    database.save_translations(database_path, [translation_of(make_article("a", now), "English")])
    database.save_translations(database_path, [translation_of(make_article("a", now), "English again")])  # still 1 article
    database.record_translation_usage(database_path, input_tokens=1000, output_tokens=200, cost_dollars=0.0002)
    with database.open_database(database_path) as connection:
        connection.execute("INSERT INTO monthly_usage (month, input_tokens, cost_dollars) VALUES ('1999-12', 50, 0.1)")

    usage = client.get("/usage").json()

    assert usage["month"] == now.strftime("%Y-%m")
    assert usage["this_month"] == {
        "articles_stored": 3,
        "articles_translated": 1,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cost_dollars": pytest.approx(0.0002),
    }
    assert usage["all_time"]["articles_stored"] == 4
    assert usage["all_time"]["articles_translated"] == 1
    assert usage["all_time"]["input_tokens"] == 1050
    assert usage["all_time"]["cost_dollars"] == pytest.approx(0.1002)
    # A month with only spending and a month with only articles both appear.
    assert list(usage["by_month"]) == ["1999-12", "2000-01", now.strftime("%Y-%m")]
    assert usage["by_month"]["2000-01"]["articles_stored"] == 1


def test_deleted_articles_leave_the_article_counts_but_not_the_spending(client, database_path):
    long_ago = datetime(2000, 1, 15, tzinfo=UTC)
    database.save_articles(database_path, [make_article("old", long_ago)])
    database.record_translation_usage(database_path, input_tokens=1000, output_tokens=200, cost_dollars=0.0002)

    database.delete_articles_published_before(database_path, datetime(2001, 1, 1, tzinfo=UTC))

    all_time = client.get("/usage").json()["all_time"]
    assert all_time["articles_stored"] == 0
    assert all_time["input_tokens"] == 1000


def test_an_edited_headline_loses_its_translation_and_a_late_translation_of_the_old_text_is_dropped(database_path):
    database.create_tables(database_path)
    published_at = datetime(2026, 10, 29, 10, 0, tzinfo=UTC)
    original = make_article("same", published_at, headline="ישנה")
    database.save_articles(database_path, [original])
    database.save_translations(database_path, [translation_of(original, "Old")])

    def stored_english_headline():
        return database.list_newest_articles(database_path, 1, ["ynet"])[0].english_headline

    database.save_articles(database_path, [original])
    assert stored_english_headline() == "Old"  # unchanged text keeps its translation

    database.save_articles(database_path, [make_article("same", published_at, headline="חדשה")])
    assert stored_english_headline() is None

    assert database.save_translations(database_path, [translation_of(original, "Old again")]) == 0
    assert stored_english_headline() is None


def test_create_tables_adds_the_translation_columns_to_an_older_database(database_path):
    connection = sqlite3.connect(database_path)
    connection.execute(
        "CREATE TABLE articles (feed_name TEXT NOT NULL, guid TEXT NOT NULL, url TEXT NOT NULL, headline TEXT NOT NULL,"
        " summary TEXT NOT NULL, published_at TEXT NOT NULL, PRIMARY KEY (feed_name, guid))"
    )
    connection.close()

    database.create_tables(database_path)
    database.save_articles(database_path, [make_article("a", datetime(2026, 10, 29, tzinfo=UTC))])

    assert database.list_newest_articles(database_path, 1, ["ynet"])[0].english_headline is None


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
