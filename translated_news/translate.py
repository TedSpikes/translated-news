"""Translate an article's headline and summary into English with Claude Haiku, one article per request.

One article per request, run several at a time, measured better than batching on 2026-10-30: batches of 5
and of 60 each made serious mistakes (a wrong party, a quote given to the wrong politician) that single
articles didn't, for about half a cent more per 60 articles.
"""

import functools
import json
import logging
import os
from dataclasses import dataclass

import anthropic
from pydantic import BaseModel

from translated_news.database import ArticleTranslation
from translated_news.feeds import Article

logger = logging.getLogger(__name__)

TRANSLATION_MODEL = "claude-haiku-5-5"
# One article came to about 165 output tokens when measured; a reply cut off short can't be parsed.
MAX_OUTPUT_TOKENS = 2048
# Claude Haiku 5.5's prices for prompts of up to 100,000 tokens (ours are about 1,000), checked 2026-10-30.
DOLLARS_PER_MILLION_INPUT_TOKENS = 0.10
DOLLARS_PER_MILLION_OUTPUT_TOKENS = 0.50

SYSTEM_PROMPT = """You translate Israeli news from Hebrew into English for a reader who follows Israeli politics, business and culture.

You get one article as JSON: its headline and its summary (the short teaser shown under the headline). Translate both into natural English news style:

- Keep the meaning. Don't add facts, explanations or opinions, and don't leave anything out.
- Spell people, parties, places and companies the way English-language Israeli media do, for example Netanyahu, Yesh Atid, Be'er Sheva, Bank Hapoalim.
- Use the names English readers know for Hebrew abbreviations and institutions, for example צה"ל is the IDF, שב"כ is the Shin Bet, ח"כ is MK, בג"ץ is the High Court of Justice.
- Keep numbers, amounts, percentages and quotations. Write shekel amounts like "NIS 50 million".
- Keep the separators between the points of a summary (" • ", " ■ ", " * ") where they are.
- An empty summary stays empty."""


class TranslationError(Exception):
    """Translation can't run at all, for example because there's no API key."""


class TranslatedArticle(BaseModel):
    english_headline: str
    english_summary: str


@dataclass(frozen=True)
class TranslationOutcome:
    """What one request to Claude produced. Tokens are billed whether or not a translation came back."""

    translation: ArticleTranslation | None
    failure: str | None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_dollars(self) -> float:
        return (
            self.input_tokens * DOLLARS_PER_MILLION_INPUT_TOKENS + self.output_tokens * DOLLARS_PER_MILLION_OUTPUT_TOKENS
        ) / 1_000_000


@functools.cache
def claude_client() -> anthropic.AsyncAnthropic:
    # Created on first use, so the server starts, and the tests run, without an API key.
    return anthropic.AsyncAnthropic(timeout=60)


def require_api_key() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise TranslationError("ANTHROPIC_API_KEY is not set; start the server through `op run` (see the README)")


async def translate_article(article: Article) -> TranslationOutcome:
    """One request to Claude for one article. Problems with the request come back as an outcome with
    `failure` set, never as an exception, so the other articles carry on and the tokens still get counted."""
    request = {"headline": article.headline, "summary": article.summary}
    try:
        response = await claude_client().messages.parse(
            model=TRANSLATION_MODEL,
            max_tokens=MAX_OUTPUT_TOKENS,
            thinking={"type": "disabled"},
            system=SYSTEM_PROMPT,
            # ensure_ascii=False sends the Hebrew as Hebrew; \u escapes would cost several times the tokens.
            messages=[{"role": "user", "content": json.dumps(request, ensure_ascii=False)}],
            output_format=TranslatedArticle,
        )
    except (anthropic.APIError, ValueError) as error:
        logger.warning("Translating %s/%s failed: %s", article.feed_name, article.guid, error)
        return TranslationOutcome(translation=None, failure=f"the request to Claude failed: {error}")

    tokens = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    if response.stop_reason == "refusal":
        category = response.stop_details.category if response.stop_details else None
        logger.warning("Claude declined %s/%s (safety category: %s)", article.feed_name, article.guid, category)
        return TranslationOutcome(translation=None, failure=f"Claude declined it (safety category: {category})", **tokens)
    if response.stop_reason != "end_turn" or response.parsed_output is None:
        logger.warning("Incomplete reply for %s/%s (stop reason: %s)", article.feed_name, article.guid, response.stop_reason)
        return TranslationOutcome(
            translation=None, failure=f"Claude's reply was incomplete (stop reason: {response.stop_reason})", **tokens
        )

    translated = response.parsed_output
    translation = ArticleTranslation(
        feed_name=article.feed_name,
        guid=article.guid,
        source_headline=article.headline,
        source_summary=article.summary,
        english_headline=translated.english_headline,
        english_summary=translated.english_summary if article.summary else "",
    )
    return TranslationOutcome(translation=translation, failure=None, **tokens)
