"""The web server. Serves the page and the stored articles, refreshes the feeds when the page opens,
translates the articles the page shows first, and reports usage totals."""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, get_args

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime

from translated_news import database
from translated_news.feeds import FEEDS, Article, Category, Site
from translated_news.refresh import refresh_feeds
from translated_news.translate import TranslationError, TranslationOutcome, require_api_key, translate_article

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

FRONTEND_DIRECTORY = Path(__file__).resolve().parent.parent / "frontend"
# Reloading the page, or opening it in several tabs, shouldn't fetch every feed again each time.
MINIMUM_TIME_BETWEEN_PAGE_REFRESHES = timedelta(minutes=1)
# Only the newest articles of each column are translated, so translations are paid for only where they're read.
# frontend/app.js loads the same number per page.
ARTICLES_TRANSLATED_PER_CATEGORY = 20
# Requests to Claude in flight at once, across all page opens. With 5, a page of 60 took about 23 seconds.
CONCURRENT_TRANSLATION_REQUESTS = 10


@dataclass(frozen=True, kw_only=True)
class ArticleWithSite(Article):
    site: Site


@dataclass(frozen=True)
class RefreshResult:
    refreshed: bool
    articles_fetched: int


@dataclass(frozen=True)
class UsageReport:
    month: str  # the current month, 'YYYY-MM' in UTC
    this_month: database.UsageTotals
    all_time: database.UsageTotals
    by_month: dict[str, database.UsageTotals]


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

    translation_slots = asyncio.Semaphore(CONCURRENT_TRANSLATION_REQUESTS)
    # Articles with a request to Claude in flight, so a second tab opening meanwhile doesn't pay for them again.
    articles_being_translated: set[tuple[str, str]] = set()
    # Holds the translation tasks until they finish, even after the page that started them has gone.
    running_translation_tasks: set[asyncio.Task] = set()

    async def translate_and_save(article: Article) -> TranslationOutcome:
        try:
            async with translation_slots:
                outcome = await translate_article(article)
            database.record_translation_usage(database_path, outcome.input_tokens, outcome.output_tokens, outcome.cost_dollars)
            if outcome.translation:
                database.save_translations(database_path, [outcome.translation])
            return outcome
        finally:
            articles_being_translated.discard((article.feed_name, article.guid))

    @app.post("/translate")
    async def translate() -> StreamingResponse:
        """Translate the newest 20 articles of each category that have no translation yet, one request to Claude per
        article, several at a time. The page calls this once its columns are showing. The response has one JSON object
        per line, sent as each article finishes: {"translation": {...}} or {"failure": "..."}."""
        untranslated = [
            article
            for category in get_args(Category)
            for article in database.list_newest_articles(
                database_path,
                ARTICLES_TRANSLATED_PER_CATEGORY,
                feed_names=[feed.name for feed in FEEDS if feed.category == category],
            )
            if article.english_headline is None and (article.feed_name, article.guid) not in articles_being_translated
        ]
        if untranslated:
            try:
                require_api_key()
            except TranslationError as error:
                raise HTTPException(502, f"Couldn't translate: {error}") from error

        # Tasks of their own, so if the page closes midway the rest are still translated, saved and counted.
        articles_being_translated.update((article.feed_name, article.guid) for article in untranslated)
        tasks = [asyncio.create_task(translate_and_save(article)) for article in untranslated]
        running_translation_tasks.update(tasks)
        for task in tasks:
            task.add_done_callback(running_translation_tasks.discard)

        async def one_line_per_article():
            for finished in asyncio.as_completed(tasks):
                outcome = await finished
                message = {"translation": asdict(outcome.translation)} if outcome.translation else {"failure": outcome.failure}
                yield json.dumps(message, ensure_ascii=False) + "\n"

        return StreamingResponse(one_line_per_article(), media_type="application/x-ndjson")

    @app.get("/usage")
    def get_usage() -> UsageReport:
        """This month, all time, and per month (UTC): articles in the database and how many are translated, by the month
        they were published, and Claude's tokens and cost, by the month they were spent."""
        by_month = database.usage_by_month(database_path)
        all_time = database.UsageTotals(
            **{field.name: sum(getattr(totals, field.name) for totals in by_month.values()) for field in fields(database.UsageTotals)}
        )
        month = database.current_month()
        return UsageReport(month=month, this_month=by_month.get(month, database.UsageTotals()), all_time=all_time, by_month=by_month)

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
