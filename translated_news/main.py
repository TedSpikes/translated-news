"""The web server. Serves the page and the stored articles, and refreshes the feeds when the page opens."""

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime

from translated_news import database
from translated_news.feeds import FEEDS, Article, Category, Site
from translated_news.refresh import refresh_feeds

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONTEND_DIRECTORY = Path(__file__).resolve().parent.parent / "frontend"
# Reloading the page, or opening it in several tabs, shouldn't fetch every feed again each time.
MINIMUM_TIME_BETWEEN_PAGE_REFRESHES = timedelta(minutes=1)


@dataclass(frozen=True)
class ArticleWithSite(Article):
    site: Site


@dataclass(frozen=True)
class RefreshResult:
    refreshed: bool
    articles_fetched: int


def create_app(database_path: Path) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.create_tables(database_path)
        yield

    app = FastAPI(title="translated-news", lifespan=lifespan)
    refresh_lock = asyncio.Lock()
    last_refreshed_at: datetime | None = None

    @app.post("/refresh")
    async def refresh() -> RefreshResult:
        """Fetch every feed now, unless this server already did within the last minute. The page calls this when it opens."""
        nonlocal last_refreshed_at
        # Requests that arrive during a refresh wait for it, then find it recent and skip.
        async with refresh_lock:
            if last_refreshed_at and datetime.now(UTC) - last_refreshed_at < MINIMUM_TIME_BETWEEN_PAGE_REFRESHES:
                return RefreshResult(refreshed=False, articles_fetched=0)
            articles_fetched = await refresh_feeds(database_path)
            last_refreshed_at = datetime.now(UTC)
            return RefreshResult(refreshed=True, articles_fetched=articles_fetched)

    @app.get("/articles")
    def get_articles(
        category: Category | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        older_than_published_at: AwareDatetime | None = None,
        older_than_feed_name: str | None = None,
        older_than_guid: str | None = None,
    ) -> list[ArticleWithSite]:
        """The newest articles, newest first: from one category, or from every feed if no category is given.

        For the next page, pass the last article's published_at, feed_name and guid as the three older_than_ parameters.
        """
        older_than_parts = (older_than_published_at, older_than_feed_name, older_than_guid)
        parts_given = [part is not None for part in older_than_parts]
        if any(parts_given) and not all(parts_given):
            raise HTTPException(422, "Pass all three older_than_ parameters, or none of them.")
        older_than = database.ArticlePosition(*older_than_parts) if all(parts_given) else None

        feeds_by_name = {feed.name: feed for feed in FEEDS if category is None or feed.category == category}
        articles = database.list_newest_articles(database_path, limit, feed_names=list(feeds_by_name), older_than=older_than)
        return [ArticleWithSite(**asdict(article), site=feeds_by_name[article.feed_name].site) for article in articles]

    # Mounted last so the API routes above take priority over files with the same path.
    app.mount("/", StaticFiles(directory=FRONTEND_DIRECTORY, html=True), name="frontend")

    return app


app = create_app(database.database_path_from_environment())
