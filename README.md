# translated-news
Israeli news headlines with automatic translation

## Running it

Needs [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run uvicorn translated_news.main:app --reload
```

Then open http://127.0.0.1:8000/ for the page: finance, politics and entertainment in three columns. It follows the system's light/dark setting.

## How articles stay fresh

Articles from the feeds in `translated_news/feeds.py` are stored in SQLite, and the page always reads from there.

1. **When the page opens**, it asks the server to fetch every feed first (`POST /refresh`). The server does this at most once a minute; quicker reloads just show what's saved.
2. **Every 15 minutes**, a cron job fetches every feed too, so the archive keeps growing while nobody is looking (see "On the VPS" below).
3. **Each fetch deletes articles published more than a year ago**, so the database can't grow without limit.

To fetch once by hand: `uv run python -m translated_news.refresh`.

## API

`GET /articles?category=finance&limit=10` returns the newest articles (leave out `category` for all feeds): headline, the first three sentences of the feed's teaser, link, publish time, and the site's name and icon. For the next page of older articles, pass the last article's `published_at`, `feed_name` and `guid` as `older_than_published_at`, `older_than_feed_name` and `older_than_guid`. Older articles come from the server's own database, not from the feeds (an RSS feed only ever holds its newest 10–100 items), so they go back only to when the server started collecting.

`POST /refresh` fetches every feed now, unless the server already did in the last minute.

Interactive API docs are at `/docs`. The page itself is plain HTML, CSS and JavaScript in `frontend/`, served by the same server.

## Settings

| Environment variable | Default |
| --- | --- |
| `TRANSLATED_NEWS_DATABASE_PATH` | `data/articles.db` inside this folder |

## On the VPS

An installation script for the VPS is planned; until then, this is what it needs to set up. Nothing here is set up on a development machine.

1. Run the web server as a service (for example with systemd): `uv run uvicorn translated_news.main:app --host 127.0.0.1 --port 8000` from this folder.
2. Add a cron job that fetches the feeds every 15 minutes (`crontab -e`):

   ```
   */15 * * * * cd /path/to/translated-news && /full/path/to/uv run python -m translated_news.refresh 2>&1 | logger -t translated-news-refresh
   ```

   - Cron runs with a minimal `PATH`, so give uv's full path (`which uv` shows it).
   - If the web server has `TRANSLATED_NEWS_DATABASE_PATH` set, set the same value on the cron line, or the two will use different databases.
   - The output goes to the system log, which rotates on its own; read it with `journalctl -t translated-news-refresh`.

## Tests

```sh
uv run pytest
```
