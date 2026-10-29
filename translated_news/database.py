"""SQLite storage for fetched articles."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from translated_news.feeds import Article

# Anchored to the repo, not the current folder, so the web server and the cron job always open the same file.
DEFAULT_DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "articles.db"


def database_path_from_environment() -> Path:
    return Path(os.environ.get("TRANSLATED_NEWS_DATABASE_PATH", DEFAULT_DATABASE_PATH))

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


def delete_articles_published_before(database_path: Path, cutoff: datetime) -> int:
    """Returns how many articles were deleted."""
    with open_database(database_path) as connection:
        return connection.execute(
            "DELETE FROM articles WHERE published_at < ?", (cutoff.astimezone(UTC).isoformat(),)
        ).rowcount


@dataclass(frozen=True)
class ArticlePosition:
    """An article's place in the newest-first order. All three parts are needed:
    several articles can share a publish time (Globes rounds to the minute)."""

    published_at: datetime
    feed_name: str
    guid: str


def list_newest_articles(
    database_path: Path, limit: int, feed_names: list[str], older_than: ArticlePosition | None = None
) -> list[Article]:
    """Newest first. With older_than, only the articles that come after that position, for loading the next page."""
    placeholders = ", ".join("?" for _ in feed_names)
    older_than_condition, older_than_values = "", ()
    if older_than:
        older_than_condition = "AND (published_at, feed_name, guid) < (?, ?, ?)"
        older_than_values = (older_than.published_at.astimezone(UTC).isoformat(), older_than.feed_name, older_than.guid)
    with open_database(database_path) as connection:
        rows = connection.execute(
            f"""
            SELECT feed_name, guid, url, headline, summary, published_at
            FROM articles WHERE feed_name IN ({placeholders}) {older_than_condition}
            ORDER BY published_at DESC, feed_name DESC, guid DESC LIMIT ?
            """,
            (*feed_names, *older_than_values, limit),
        ).fetchall()
    return [Article(**dict(row) | {"published_at": datetime.fromisoformat(row["published_at"])}) for row in rows]
