# translated-news
Israeli news headlines with automatic translation

## Running it

Needs [uv](https://docs.astral.sh/uv/), and for translations the [1Password CLI](https://developer.1password.com/docs/cli/) (see "API key" below).

```sh
uv sync
op run --env-file=1password.env -- uv run uvicorn translated_news.main:app --reload
```

Then open http://127.0.0.1:8000/ for the page: finance, politics and entertainment in three columns. It follows the system's light/dark setting. Without `op run …` the server still works, but shows the Hebrew originals and a note that translation needs the API key.

## API key

Translations use the Claude API. The SDK reads the key from the `ANTHROPIC_API_KEY` environment variable; it is never in the code, the repo, or the browser.

- **On a development Mac**, the key lives in 1Password as the item "Claude translated-news-api-key" (vault "Personal"). `1password.env` holds only a reference to it, so it's safe to commit; `op run --env-file=1password.env -- <command>` puts the real key into that one command's environment, after a Touch ID prompt. Don't export the key in your shell profile: every program would see it, including Claude Code, which would then bill it.
- **On the VPS**, see step 2 of "On the VPS".
- **In the Claude Console**, give the app its own workspace with a monthly spend limit (the Default Workspace can't have one), and give each machine its own key in that workspace, so one can be revoked without the other.

## How translation works

When the page opens, it shows each column's first 20 articles straight away, with the English where a translation is already saved and the Hebrew where not. It then calls `POST /translate`. The server collects those first 20 of each column that have no translation yet (at most 60) and sends **each article as its own request** to Claude Haiku (`claude-haiku-5-5`, thinking off), up to 10 at a time, as JSON, getting JSON back in a fixed shape (structured outputs). As each one finishes, the server saves it next to its article and streams it to the page, which swaps it in.

One request per article was chosen by measurement (2026-10-30, 60 articles, 4 runs per setup): sending all 60 in one request, or in batches of 5, produced wrong parties and people (for example a quote given to the wrong politician) that single-article requests didn't, at about 1 cent instead of 0.6–0.7 cents per 60 articles.

- Each article is translated once. If a site edits a headline or summary, the next refresh clears that translation and the following page open translates the new text.
- Articles past the first 20 ("Load older articles") aren't translated and show in Hebrew, so loading them costs nothing.
- Two tabs opening at once don't pay twice: articles with a request already in flight are skipped.
- If the page is closed midway, the server still finishes, saves and counts the translations it started.
- If an article fails (network, or Claude declining it), it stays in Hebrew, the page says how many failed, and the next page open tries again. Without an API key the page says so and shows the Hebrew.
- The cron job never translates and never needs the key.

## Usage totals

The page's top-right corner shows, in grey, this month's and all-time totals (calendar months in UTC):

- **Articles stored and translated** are counted live from the articles table: how many are in the database, and how many of those have a translation, by the month they were published. Articles removed by the one-year delete no longer count, so "all time" here means everything still stored.
- **Claude tokens in and out, and their cost in dollars**, come from the `monthly_usage` table, which adds up each request as it's made (Haiku 5.5 at $0.10 / $0.50 per million input / output tokens, as of 2026-10-30). These can't be worked out afterwards, so they're kept apart from the articles and the one-year delete doesn't touch them. Counting started on 2026-10-30.

## How articles stay fresh

Articles from the feeds in `translated_news/feeds.py` are stored in SQLite, and the page always reads from there.

1. **When the page opens**, it asks the server to fetch every feed first (`POST /refresh`). The server does this at most once a minute; quicker reloads just show what's saved.
2. **Every 15 minutes**, a cron job fetches every feed too, so the archive keeps growing while nobody is looking (see "On the VPS" below).
3. **Each fetch deletes articles published more than a year ago**, so the database can't grow without limit.

To fetch once by hand: `uv run python -m translated_news.refresh`.

## API

`GET /articles?category=finance&limit=10` returns the newest articles (leave out `category` for all feeds): headline, the first three sentences of the feed's teaser, link, publish time, and the site's name and icon. For the next page of older articles, pass the last article's `published_at`, `feed_name` and `guid` as `older_than_published_at`, `older_than_feed_name` and `older_than_guid`. Older articles come from the server's own database, not from the feeds (an RSS feed only ever holds its newest 10–100 items), so they go back only to when the server started collecting.

`POST /refresh` fetches every feed now, unless the server already did in the last minute.

`POST /translate` translates the newest 20 untranslated articles of each category, one Claude request per article, and streams the results as newline-delimited JSON, one line per article as it finishes: `{"translation": {...}}` or `{"failure": "..."}`. Articles from `GET /articles` carry `english_headline` and `english_summary`, `null` until translated.

`GET /usage` returns the usage totals: `this_month`, `all_time` and `by_month`, each with `articles_stored`, `articles_translated`, `input_tokens`, `output_tokens` and `cost_dollars`.

Interactive API docs are at `/docs`. The page itself is plain HTML, CSS and JavaScript in `frontend/`, served by the same server.

## Settings

| Environment variable | Default |
| --- | --- |
| `TRANSLATED_NEWS_DATABASE_PATH` | `data/articles.db` inside this folder |
| `ANTHROPIC_API_KEY` | none; needed only by the web server, for translations |

## On the VPS

An installation script for the VPS is planned; until then, this is what it needs to set up. Nothing here is set up on a development machine.

1. Run the web server as a service under its own unprivileged user (for example with systemd): `uv run uvicorn translated_news.main:app --host 127.0.0.1 --port 8000` from this folder.
2. Give it the API key through a file only root can read, say `/etc/translated-news/secrets.env` (owner root, mode `600`), containing `ANTHROPIC_API_KEY=…`, loaded with `EnvironmentFile=/etc/translated-news/secrets.env` in the service's unit. systemd reads the file as root and hands the key to the server only. Write the file from a prompt that doesn't echo (`read -s`), never from a command argument, which would land in the shell history.
3. Add a cron job that fetches the feeds every 15 minutes (`crontab -e`):

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
