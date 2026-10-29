from datetime import UTC, datetime

import pytest

from translated_news.feeds import Article, Feed, first_sentences, parse_feed

# Same shape as the real Ynet feed: CDATA everywhere, and an image link before the teaser.
YNET_STYLE_FEED = """<?xml version='1.0' encoding='utf-8' ?>
<rss version="2.0"><channel><title>ynet</title>
<item>
    <title><![CDATA[כותרת ראשית ]]></title>
    <description><![CDATA[<div><a href='https://www.ynet.co.il/news/article/abc'><img src='https://example.com/a.jpg' width='100'></a></div>משפט ראשון. משפט שני! משפט שלישי? משפט רביעי.]]></description>
    <link><![CDATA[https://www.ynet.co.il/news/article/abc]]></link>
    <pubDate><![CDATA[Thu, 29 Oct 2026 15:52:48 +0200]]></pubDate>
    <guid><![CDATA[https://www.ynet.co.il/news/article/abc]]></guid>
</item>
</channel></rss>""".encode()

# Same shape as the real Globes feed: a numeric guid, and bullets escaped twice.
GLOBES_STYLE_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0"><channel><title>globes</title>
<item>
    <title>כותרת</title>
    <link>https://www.globes.co.il/news/article.aspx?did=1001561197#utm_source=RSS</link>
    <guid isPermaLink="false">1001561197</guid>
    <description>נקודה ראשונה &amp;#8226; נקודה שנייה &amp;#8226; נקודה שלישית &amp;#8226; נקודה רביעית</description>
    <pubDate>Thu, 29 Oct 2026 13:30:00 GMT</pubDate>
</item>
</channel></rss>""".encode()


def test_parses_ynet_style_item():
    [article] = parse_feed(Feed(name="ynet", url="unused"), YNET_STYLE_FEED)
    assert article == Article(
        feed_name="ynet",
        guid="https://www.ynet.co.il/news/article/abc",
        url="https://www.ynet.co.il/news/article/abc",
        headline="כותרת ראשית",
        summary="משפט ראשון. משפט שני! משפט שלישי?",
        published_at=datetime(2026, 10, 29, 13, 52, 48, tzinfo=UTC),
    )


def test_parses_globes_style_item():
    [article] = parse_feed(Feed(name="globes", url="unused"), GLOBES_STYLE_FEED)
    assert article.guid == "1001561197"
    assert article.summary == "נקודה ראשונה • נקודה שנייה • נקודה שלישית"
    assert article.published_at == datetime(2026, 10, 29, 13, 30, tzinfo=UTC)


def test_unparseable_feed_raises():
    with pytest.raises(ValueError, match="Could not parse feed broken"):
        parse_feed(Feed(name="broken", url="unused"), b"<html>not a feed")


@pytest.mark.parametrize(
    ("text", "expected_first_sentence"),
    [
        ("המניה עלתה ב-0.5% היום. עוד משפט.", "המניה עלתה ב-0.5% היום."),
        ('נאמר: "אנחנו לא שעיר לעזאזל." הדובר הוסיף.', 'נאמר: "אנחנו לא שעיר לעזאזל."'),
        ('יו"ר הוועדה אמר. עוד משפט.', 'יו"ר הוועדה אמר.'),
        ("רק משפט אחד בלי נקודה", "רק משפט אחד בלי נקודה"),
        ("נקודה ראשונה ■ נקודה שנייה", "נקודה ראשונה"),
        ("נקודה ראשונה * נקודה שנייה", "נקודה ראשונה"),
    ],
)
def test_first_sentence(text, expected_first_sentence):
    assert first_sentences(text, 1) == expected_first_sentence


def test_short_text_is_kept_whole():
    assert first_sentences("אחד. שניים.", 3) == "אחד. שניים."
