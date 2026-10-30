"""Fetch every feed once, save the articles, and delete the ones older than a year.

The web server runs this when the page is opened. On the VPS, cron also runs it every 15 minutes:

    uv run python -m translated_news.refresh
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from translated_news import database
from translated_news.feeds import fetch_all_feeds

logger = logging.getLogger(__name__)

# Keeps the database from growing forever if the app is left running and forgotten.
ARTICLE_RETENTION = timedelta(days=365)


async def refresh_feeds(database_path: Path) -> int:
    """Returns how many articles the feeds held."""
    articles = await fetch_all_feeds()
    new_count = database.save_articles(database_path, articles)
    deleted_count = database.delete_articles_published_before(database_path, datetime.now(UTC) - ARTICLE_RETENTION)
    logger.info(
        "Saved %d articles (%d new), deleted %d older than %d days", len(articles), new_count, deleted_count, ARTICLE_RETENTION.days
    )
    return len(articles)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    database_path = database.database_path_from_environment()
    database.create_tables(database_path)
    asyncio.run(refresh_feeds(database_path))


if __name__ == "__main__":
    main()
