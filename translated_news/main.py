"""The web server. Serves stored articles and refreshes the feeds in the background."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query

from translated_news import database
from translated_news.feeds import Article, fetch_all_feeds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def refresh_feeds_forever(database_path: Path, refresh_interval_seconds: float) -> None:
    while True:
        try:
            articles = await fetch_all_feeds()
            database.save_articles(database_path, articles)
            logger.info("Saved %d articles", len(articles))
        except Exception:
            logger.exception("Feed refresh failed; will retry in %s seconds", refresh_interval_seconds)
        await asyncio.sleep(refresh_interval_seconds)


def create_app(database_path: Path, refresh_interval_seconds: float, refresh_feeds_in_background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.create_tables(database_path)
        refresh_task = None
        if refresh_feeds_in_background:
            refresh_task = asyncio.create_task(refresh_feeds_forever(database_path, refresh_interval_seconds))
        yield
        if refresh_task:
            refresh_task.cancel()

    app = FastAPI(title="translated-news", lifespan=lifespan)

    @app.get("/articles")
    def get_articles(limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[Article]:
        """The newest articles from all feeds, newest first."""
        return database.list_newest_articles(database_path, limit)

    return app


app = create_app(
    database_path=Path(os.environ.get("TRANSLATED_NEWS_DATABASE_PATH", "data/articles.db")),
    refresh_interval_seconds=float(os.environ.get("TRANSLATED_NEWS_REFRESH_INTERVAL_SECONDS", "600")),
)
