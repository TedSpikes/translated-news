"""SQLite storage for fetched articles."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from translated_news.feeds import Article

# published_at is stored as ISO 8601 in UTC, so sorting the text sorts by time.
CREATE_ARTICLES_TABLE = """
CREATE TABLE IF NOT EXISTS articles (
    feed_name TEXT NOT NULL,
    guid TEXT NOT NULL,
    url TEXT NOT NULL,
    headline TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_at TEXT NOT NULL,
    PRIMARY KEY (feed_name, guid)
)
"""


@contextmanager
def open_database(database_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a connection, commit on success or roll back on error, and always close it."""
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def create_tables(database_path: Path) -> None:
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with open_database(database_path) as connection:
        connection.execute(CREATE_ARTICLES_TABLE)


def save_articles(database_path: Path, articles: list[Article]) -> None:
    """Insert new articles. An article already stored gets its headline and summary updated,
    because news sites edit them as a story develops."""
    with open_database(database_path) as connection:
        connection.executemany(
            """
            INSERT INTO articles (feed_name, guid, url, headline, summary, published_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (feed_name, guid) DO UPDATE SET
                url = excluded.url, headline = excluded.headline, summary = excluded.summary
            """,
            [
                (article.feed_name, article.guid, article.url, article.headline, article.summary, article.published_at.isoformat())
                for article in articles
            ],
        )


def list_newest_articles(database_path: Path, limit: int) -> list[Article]:
    with open_database(database_path) as connection:
        rows = connection.execute(
            """
            SELECT feed_name, guid, url, headline, summary, published_at
            FROM articles ORDER BY published_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [Article(**dict(row) | {"published_at": datetime.fromisoformat(row["published_at"])}) for row in rows]
