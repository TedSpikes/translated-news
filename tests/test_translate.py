import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from translated_news import translate
from translated_news.feeds import Article
from translated_news.translate import TranslatedArticle, TranslationError, require_api_key, translate_article


def make_article(headline: str = "כותרת", summary: str = "תקציר.") -> Article:
    return Article(
        feed_name="ynet",
        guid="a",
        url="https://www.ynet.co.il/news/article/a",
        headline=headline,
        summary=summary,
        published_at=datetime(2026, 10, 30, 9, 0, tzinfo=UTC),
    )


def make_reply(parsed_output: TranslatedArticle | None, stop_reason: str = "end_turn") -> SimpleNamespace:
    return SimpleNamespace(
        stop_reason=stop_reason,
        stop_details=None,
        parsed_output=parsed_output,
        usage=SimpleNamespace(input_tokens=1000, output_tokens=200),
    )


class FakeMessages:
    """Stands in for client.messages: records each request, then returns `reply` or raises `error`."""

    def __init__(self):
        self.reply = None
        self.error = None
        self.requests = []

    async def parse(self, **request):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.reply


@pytest.fixture
def fake_claude(monkeypatch):
    messages = FakeMessages()
    monkeypatch.setattr(translate, "claude_client", lambda: SimpleNamespace(messages=messages))
    return messages


def test_one_article_one_request_with_thinking_off(fake_claude):
    fake_claude.reply = make_reply(TranslatedArticle(english_headline="Headline", english_summary="Summary."))

    outcome = asyncio.run(translate_article(make_article()))

    [request] = fake_claude.requests
    assert request["model"] == "claude-haiku-5-5"
    assert request["thinking"] == {"type": "disabled"}
    assert request["output_format"] is TranslatedArticle
    assert json.loads(request["messages"][0]["content"]) == {"headline": "כותרת", "summary": "תקציר."}
    assert "כותרת" in request["messages"][0]["content"]  # Hebrew sent as Hebrew, not as \u escapes
    assert outcome.failure is None
    assert outcome.translation.english_headline == "Headline"
    assert outcome.translation.source_headline == "כותרת"
    assert (outcome.input_tokens, outcome.output_tokens) == (1000, 200)
    assert outcome.cost_dollars == pytest.approx(1000 * 0.10 / 1e6 + 200 * 0.50 / 1e6)


def test_an_empty_summary_stays_empty_whatever_comes_back(fake_claude):
    fake_claude.reply = make_reply(TranslatedArticle(english_headline="Headline", english_summary="invented"))
    outcome = asyncio.run(translate_article(make_article(summary="")))
    assert outcome.translation.english_summary == ""


def test_a_refusal_is_a_failure_that_still_counts_its_tokens(fake_claude):
    fake_claude.reply = make_reply(None, stop_reason="refusal")
    outcome = asyncio.run(translate_article(make_article()))
    assert outcome.translation is None
    assert "declined" in outcome.failure
    assert (outcome.input_tokens, outcome.output_tokens) == (1000, 200)


def test_an_api_error_is_a_failure_not_an_exception(fake_claude):
    fake_claude.error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    outcome = asyncio.run(translate_article(make_article()))
    assert outcome.translation is None
    assert "failed" in outcome.failure
    assert outcome.cost_dollars == 0


def test_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(TranslationError, match="ANTHROPIC_API_KEY"):
        require_api_key()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    require_api_key()
