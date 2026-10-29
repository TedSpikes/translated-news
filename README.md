# translated-news
Israeli news headlines with automatic translation

## Running it

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run uvicorn translated_news.main:app --reload
```

Then open http://127.0.0.1:8000/ for the page: finance, politics and entertainment in three columns. It follows the system's light/dark setting.

The server fetches every feed in `translated_news/feeds.py` when it starts and then every 10 minutes, and stores the articles in SQLite. `GET /articles?category=finance&limit=10` returns the newest ones (leave out `category` for all feeds): headline, the first three sentences of the feed's teaser, link, publish time, and the site's name and icon. Interactive API docs are at `/docs`. The page itself is plain HTML, CSS and JavaScript in `frontend/`, served by the same server.

Settings (environment variables):

| Variable | Default |
| --- | --- |
| `TRANSLATED_NEWS_DATABASE_PATH` | `data/articles.db` |
| `TRANSLATED_NEWS_REFRESH_INTERVAL_SECONDS` | `600` |

## Tests

```sh
uv run pytest
```
