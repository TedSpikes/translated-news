"""Fetch the news RSS feeds and turn their items into articles."""

import asyncio
import html
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

import feedparser
import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "translated-news/0.1 (+https://github.com/TedSpikes/translated-news)"
SUMMARY_SENTENCE_COUNT = 3

HTML_TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")
# A sentence ends at . ! or ? (maybe followed by a closing quote) and then a space.
# Some sites separate the points of a teaser with a bullet instead of a full stop:
# Globes uses " • ", TheMarker " ■ " and Zman Yisrael " * ".
SENTENCE_SEPARATOR = re.compile(r'((?<=[.!?])\s+|(?<=[.!?]["”])\s+|\s+[•■*]\s+)')


Category = Literal["finance", "politics", "entertainment"]


@dataclass(frozen=True)
class Site:
    name: str
    favicon_url: str


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    category: Category
    site: Site


# Ynet, Haaretz and TheMarker don't serve their icon at /favicon.ico; these are the ones their homepages link to.
YNET = Site(name="Ynet", favicon_url="https://www.ynet.co.il/images/favicon/favicon_1.ico")
WALLA = Site(name="Walla", favicon_url="https://www.walla.co.il/favicon.ico")
HAARETZ = Site(name="Haaretz", favicon_url="https://www.haaretz.co.il/v2/htz-bucket/static/favicon.ico")
ZMAN_YISRAEL = Site(name="Zman Yisrael", favicon_url="https://www.zman.co.il/favicon.ico")
GLOBES = Site(name="Globes", favicon_url="https://www.globes.co.il/favicon.ico")
THEMARKER = Site(name="TheMarker", favicon_url="https://www.themarker.com/v2/tm-bucket/static/favicon.ico")

FEEDS = [
    Feed(name="ynet", url="https://www.ynet.co.il/Integration/StoryRss2.xml", category="politics", site=YNET),
    Feed(name="ynet-economy", url="https://www.ynet.co.il/Integration/StoryRss6.xml", category="finance", site=YNET),
    Feed(name="ynet-culture", url="https://www.ynet.co.il/Integration/StoryRss538.xml", category="entertainment", site=YNET),
    Feed(name="walla-politics", url="https://www.walla.co.il/rss/feed/news/politics", category="politics", site=WALLA),
    Feed(name="haaretz", url="https://www.haaretz.co.il/srv/rss---feedly", category="politics", site=HAARETZ),
    Feed(name="zman-yisrael", url="https://www.zman.co.il/feed/", category="politics", site=ZMAN_YISRAEL),
    Feed(name="globes", url="https://www.globes.co.il/webservice/rss/rssfeeder.asmx/FeederNode?iID=2", category="finance", site=GLOBES),
    Feed(name="themarker", url="https://www.themarker.com/srv/tm-all-articles", category="finance", site=THEMARKER),
]


@dataclass(frozen=True)
class Article:
    feed_name: str
    guid: str
    url: str
    headline: str
    summary: str
    published_at: datetime


def clean_text(raw_html: str) -> str:
    """Strip HTML tags and decode entities.

    Ynet puts an image link before its teaser text. Globes escapes its bullets twice
    (`&amp;#8226;`), so they are still entities after feedparser has decoded once.
    """
    without_tags = HTML_TAG.sub(" ", raw_html)
    return WHITESPACE.sub(" ", html.unescape(without_tags)).strip()


def first_sentences(text: str, sentence_count: int) -> str:
    pieces = SENTENCE_SEPARATOR.split(text)  # sentence, separator, sentence, separator, ...
    return "".join(pieces[: 2 * sentence_count - 1])


def parse_feed(feed: Feed, feed_xml: bytes) -> list[Article]:
    parsed = feedparser.parse(feed_xml)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"Could not parse feed {feed.name}: {parsed.bozo_exception}")
    return [
        Article(
            feed_name=feed.name,
            guid=entry.get("id") or entry.link,
            url=entry.link,
            headline=clean_text(entry.title),
            summary=first_sentences(clean_text(entry.get("summary", "")), SUMMARY_SENTENCE_COUNT),
            published_at=datetime(*entry.published_parsed[:6], tzinfo=UTC),
        )
        for entry in parsed.entries
    ]


async def fetch_feed(http_client: httpx.AsyncClient, feed: Feed) -> list[Article]:
    response = await http_client.get(feed.url)
    response.raise_for_status()
    return parse_feed(feed, response.content)


async def fetch_all_feeds() -> list[Article]:
    """Fetch every feed at once. A feed that fails is logged and skipped, so one broken site doesn't block the rest."""
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=20, follow_redirects=True) as http_client:
        results = await asyncio.gather(*(fetch_feed(http_client, feed) for feed in FEEDS), return_exceptions=True)

    articles = []
    for feed, result in zip(FEEDS, results):
        if isinstance(result, BaseException):
            logger.error("Fetching feed %s failed", feed.name, exc_info=result)
            continue
        articles.extend(result)
    return articles
