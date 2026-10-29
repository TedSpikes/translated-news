# translated-news
Israeli news headlines with automatic translation

## Running it

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run uvicorn translated_news.main:app --reload
curl 'http://127.0.0.1:8000/articles?limit=10'
```

The server fetches every feed in `translated_news/feeds.py` when it starts and then every 10 minutes, and stores the articles in SQLite. `GET /articles` returns the newest ones: headline, the first three sentences of the feed's teaser, link and publish time. Interactive API docs are at `/docs`.

Settings (environment variables):

| Variable | Default |
| --- | --- |
| `TRANSLATED_NEWS_DATABASE_PATH` | `data/articles.db` |
| `TRANSLATED_NEWS_REFRESH_INTERVAL_SECONDS` | `600` |

## Tests

```sh
uv run pytest
```
