"""SQLite storage for fetched articles, their translations, and monthly usage totals."""

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
    -- NULL until translated, and cleared again when the site edits the headline or summary.
    english_headline TEXT,
    english_summary TEXT,
    PRIMARY KEY (feed_name, guid)
)
"""
# Databases created before translations existed lack these, so create_tables adds them.
TRANSLATION_COLUMNS = ("english_headline", "english_summary")

# What Claude cost, per calendar month (UTC). Unlike article counts, this can't be worked out afterwards, so it's
# added up as requests are made, and kept apart from the articles so the one-year delete doesn't touch it.
CREATE_MONTHLY_USAGE_TABLE = """
CREATE TABLE IF NOT EXISTS monthly_usage (
    month TEXT PRIMARY KEY,  -- 'YYYY-MM'
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_dollars REAL NOT NULL DEFAULT 0
)
"""


@dataclass(frozen=True)
class UsageTotals:
    # Articles in the database now, by the month they were published; deleted articles no longer count.
    articles_stored: int = 0
    articles_translated: int = 0
    # Spent on Claude in that month, from monthly_usage.
    input_tokens: int = 0
    output_tokens: int = 0
    cost_dollars: float = 0.0


@dataclass(frozen=True)
class ArticleTranslation:
    feed_name: str
    guid: str
    # The text that was translated, so a translation of text the site has since edited isn't stored.
    source_headline: str
    source_summary: str
    english_headline: str
    english_summary: str


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
        existing_columns = {row["name"] for row in connection.execute("PRAGMA table_info(articles)")}
        for column in TRANSLATION_COLUMNS:
            if column not in existing_columns:
                connection.execute(f"ALTER TABLE articles ADD COLUMN {column} TEXT")
        connection.execute(CREATE_MONTHLY_USAGE_TABLE)


def current_month() -> str:
    return datetime.now(UTC).strftime("%Y-%m")


def record_translation_usage(database_path: Path, input_tokens: int, output_tokens: int, cost_dollars: float) -> None:
    """Adds one Claude request's tokens and cost to this month's totals."""
    with open_database(database_path) as connection:
        connection.execute(
            """
            INSERT INTO monthly_usage (month, input_tokens, output_tokens, cost_dollars) VALUES (?, ?, ?, ?)
            ON CONFLICT (month) DO UPDATE SET
                input_tokens = input_tokens + excluded.input_tokens,
                output_tokens = output_tokens + excluded.output_tokens,
                cost_dollars = cost_dollars + excluded.cost_dollars
            """,
            (current_month(), input_tokens, output_tokens, cost_dollars),
        )


def usage_by_month(database_path: Path) -> dict[str, UsageTotals]:
    """Every month with articles or Claude spending, oldest first. See UsageTotals for what counts where."""
    with open_database(database_path) as connection:
        # published_at is ISO 8601 in UTC, so its first 7 characters are the month.
        article_rows = connection.execute(
            "SELECT substr(published_at, 1, 7) AS month, COUNT(*) AS stored, COUNT(english_headline) AS translated"
            " FROM articles GROUP BY month"
        ).fetchall()
        spending_rows = connection.execute("SELECT month, input_tokens, output_tokens, cost_dollars FROM monthly_usage").fetchall()
    articles = {row["month"]: row for row in article_rows}
    spending = {row["month"]: row for row in spending_rows}
    return {
        month: UsageTotals(
            articles_stored=articles[month]["stored"] if month in articles else 0,
            articles_translated=articles[month]["translated"] if month in articles else 0,
            input_tokens=spending[month]["input_tokens"] if month in spending else 0,
            output_tokens=spending[month]["output_tokens"] if month in spending else 0,
            cost_dollars=spending[month]["cost_dollars"] if month in spending else 0.0,
        )
        for month in sorted(articles.keys() | spending.keys())
    }


def save_articles(database_path: Path, articles: list[Article]) -> int:
    """Insert new articles. An article already stored gets its headline and summary updated,
    because news sites edit them as a story develops, and loses its translation if either changed.
    Returns how many articles were new."""
    with open_database(database_path) as connection:
        count_before = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        # In DO UPDATE, `articles.x` is the stored value and `excluded.x` the incoming one.
        connection.executemany(
            """
            INSERT INTO articles (feed_name, guid, url, headline, summary, published_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (feed_name, guid) DO UPDATE SET
                url = excluded.url,
                headline = excluded.headline,
                summary = excluded.summary,
                english_headline = CASE WHEN articles.headline = excluded.headline AND articles.summary = excluded.summary
                    THEN articles.english_headline END,
                english_summary = CASE WHEN articles.headline = excluded.headline AND articles.summary = excluded.summary
                    THEN articles.english_summary END
            """,
            [
                (article.feed_name, article.guid, article.url, article.headline, article.summary, article.published_at.isoformat())
                for article in articles
            ],
        )
        return connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] - count_before


def save_translations(database_path: Path, translations: list[ArticleTranslation]) -> int:
    """Store translations where the article still has the text that was translated (a refresh may have
    brought an edit in the meantime). Returns how many were stored."""
    with open_database(database_path) as connection:
        return connection.executemany(
            """
            UPDATE articles SET english_headline = ?, english_summary = ?
            WHERE feed_name = ? AND guid = ? AND headline = ? AND summary = ?
            """,
            [
                (t.english_headline, t.english_summary, t.feed_name, t.guid, t.source_headline, t.source_summary)
                for t in translations
            ],
        ).rowcount


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
            SELECT feed_name, guid, url, headline, summary, published_at, english_headline, english_summary
            FROM articles WHERE feed_name IN ({placeholders}) {older_than_condition}
            ORDER BY published_at DESC, feed_name DESC, guid DESC LIMIT ?
            """,
            (*feed_names, *older_than_values, limit),
        ).fetchall()
    return [Article(**dict(row) | {"published_at": datetime.fromisoformat(row["published_at"])}) for row in rows]
