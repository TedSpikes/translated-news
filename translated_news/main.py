"""The web server. Serves stored articles and refreshes the feeds in the background."""

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, Query
from fastapi.staticfiles import StaticFiles

from translated_news import database
from translated_news.feeds import FEEDS, Article, Category, Site, fetch_all_feeds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

FRONTEND_DIRECTORY = Path(__file__).resolve().parent.parent / "frontend"


@dataclass(frozen=True)
class ArticleWithSite(Article):
    site: Site


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
    def get_articles(category: Category | None = None, limit: Annotated[int, Query(ge=1, le=200)] = 50) -> list[ArticleWithSite]:
        """The newest articles, newest first: from one category, or from every feed if no category is given."""
        feeds_by_name = {feed.name: feed for feed in FEEDS if category is None or feed.category == category}
        articles = database.list_newest_articles(database_path, limit, feed_names=list(feeds_by_name))
        return [ArticleWithSite(**asdict(article), site=feeds_by_name[article.feed_name].site) for article in articles]

    # Mounted last so the API routes above take priority over files with the same path.
    app.mount("/", StaticFiles(directory=FRONTEND_DIRECTORY, html=True), name="frontend")

    return app


app = create_app(
    database_path=Path(os.environ.get("TRANSLATED_NEWS_DATABASE_PATH", "data/articles.db")),
    refresh_interval_seconds=float(os.environ.get("TRANSLATED_NEWS_REFRESH_INTERVAL_SECONDS", "600")),
)
