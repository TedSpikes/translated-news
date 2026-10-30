// Asks the server to fetch the feeds, then fills each column with articles from its category,
// newest first; each column's "Load older articles" button adds its next page. Once the first pages
// are showing, it asks the server to translate whatever in them isn't translated yet, and swaps
// each translation in where the original is.

// The same as ARTICLES_TRANSLATED_PER_CATEGORY on the server, so the first page is the translated set.
const ARTICLES_PER_PAGE = 20;
const HEBREW_LETTER = /[\u0590-\u05FF]/;
const relativeTime = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const resultTemplate = document.getElementById("result-template");
// Every article on the page and its element, so a translation that arrives later can replace it.
const shownArticles = new Map();

function articleKey(article) {
  return `${article.feed_name}\n${article.guid}`;
}

function showNotice(noticeId, message) {
  const notice = document.getElementById(noticeId);
  notice.textContent = message;
  notice.hidden = !message;
}

function describeAge(publishedAt) {
  const minutesAgo = Math.round((Date.now() - new Date(publishedAt)) / 60_000);
  if (minutesAgo < 1) return "just now";
  if (minutesAgo < 60) return relativeTime.format(-minutesAgo, "minute");
  const hoursAgo = Math.round(minutesAgo / 60);
  if (hoursAgo < 24) return relativeTime.format(-hoursAgo, "hour");
  return relativeTime.format(-Math.round(hoursAgo / 24), "day");
}

// "https://www.ynet.co.il/economy/article/abc" -> "ynet.co.il › economy › article"
function describeUrl(url) {
  const firstPathSegments = url.pathname.split("/").filter(Boolean).slice(0, 2);
  return [url.hostname.replace(/^www\./, ""), ...firstPathSegments].join(" › ");
}

// Feed content comes from other sites, so only plain web links are allowed as hrefs.
function parseWebUrl(text) {
  const url = URL.canParse(text) ? new URL(text) : null;
  return url && (url.protocol === "https:" || url.protocol === "http:") ? url : null;
}

function renderArticle(article) {
  const url = parseWebUrl(article.url);
  if (!url) {
    console.warn("Skipping article without a web link", article);
    return null;
  }
  const result = resultTemplate.content.firstElementChild.cloneNode(true);
  const headline = article.english_headline ?? article.headline;
  const summary = article.english_summary ?? article.summary;
  // Hebrew reads right to left; English left to right.
  result.dir = HEBREW_LETTER.test(headline) ? "rtl" : "ltr";

  const title = result.querySelector(".result-title");
  title.href = url.href;
  title.textContent = headline;

  const favicon = result.querySelector(".result-favicon");
  favicon.src = article.site.favicon_url;
  favicon.addEventListener("error", () => (favicon.style.visibility = "hidden"));

  result.querySelector(".result-site-name").textContent = article.site.name;
  result.querySelector(".result-url").textContent = describeUrl(url);
  result.querySelector(".result-age").textContent = describeAge(article.published_at);

  const teaser = result.querySelector(".result-teaser");
  teaser.textContent = summary;
  teaser.hidden = !summary;

  shownArticles.set(articleKey(article), { article, element: result });
  return result;
}

function articlesUrl(category, lastShownArticle) {
  const query = new URLSearchParams({ category, limit: ARTICLES_PER_PAGE });
  if (lastShownArticle) {
    query.set("older_than_published_at", lastShownArticle.published_at);
    query.set("older_than_feed_name", lastShownArticle.feed_name);
    query.set("older_than_guid", lastShownArticle.guid);
  }
  return `/articles?${query}`;
}

function setUpColumn(column) {
  const resultList = column.querySelector(".results");
  const status = column.querySelector(".column-status");
  const loadOlderButton = column.querySelector(".load-older");
  let lastShownArticle = null;

  function showStatus(message) {
    status.textContent = message;
    status.hidden = !message;
  }

  // The button stays hidden while a page loads, so a second click can't start a second load.
  // Returns the page's articles (none if it failed).
  async function loadNextPage() {
    loadOlderButton.hidden = true;
    showStatus("Loading…");
    try {
      const response = await fetch(articlesUrl(column.dataset.category, lastShownArticle));
      if (!response.ok) throw new Error(`the server answered ${response.status}`);
      const articles = await response.json();
      resultList.append(...articles.map(renderArticle).filter(Boolean));
      if (articles.length === ARTICLES_PER_PAGE) {
        lastShownArticle = articles.at(-1);
        showStatus("");
        loadOlderButton.hidden = false;
      } else if (lastShownArticle || articles.length) {
        showStatus("No older articles. The archive goes back to when the server started collecting.");
      } else {
        showStatus("No articles yet. The server fetches the feeds when the page opens; reload in a few seconds.");
      }
      return articles;
    } catch (error) {
      showStatus(`Couldn't load articles: ${error.message}`);
      loadOlderButton.hidden = false;  // a click tries the same page again
      return [];
    }
  }

  loadOlderButton.addEventListener("click", loadNextPage);
  return loadNextPage();
}

// If the feeds can't be fetched, the saved articles are still worth showing, with a note saying so.
async function refreshFeeds() {
  try {
    const response = await fetch("/refresh", { method: "POST" });
    if (!response.ok) throw new Error(`the server answered ${response.status}`);
  } catch (error) {
    showNotice("refresh-notice", `Couldn't check for new articles (${error.message}); showing the saved ones.`);
  }
}

// The lines of a streamed response, each as soon as it has fully arrived.
async function* responseLines(response) {
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let unfinished = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    const lines = (unfinished + value).split("\n");
    unfinished = lines.pop();
    yield* lines.filter(Boolean);
  }
  if (unfinished) yield unfinished;
}

function showTranslation(translation) {
  const shown = shownArticles.get(articleKey(translation));
  if (!shown) return;
  const translated = { ...shown.article, english_headline: translation.english_headline, english_summary: translation.english_summary };
  shown.element.replaceWith(renderArticle(translated));
}

// Runs after the first pages are showing, so the page never waits for Claude. The server translates
// one article per request and sends each result as it finishes; until then an article shows its
// Hebrew original.
async function translateFirstPages(firstPageArticles) {
  const untranslatedCount = firstPageArticles.filter((article) => article.english_headline == null).length;
  if (untranslatedCount === 0) return;
  const failures = [];
  let translatedCount = 0;
  showNotice("translation-notice", `Translating ${untranslatedCount} new articles…`);
  try {
    const response = await fetch("/translate", { method: "POST" });
    if (!response.ok) {
      const body = await response.json().catch(() => null);
      throw new Error(body?.detail ?? `the server answered ${response.status}`);
    }
    for await (const line of responseLines(response)) {
      const message = JSON.parse(line);
      if (message.translation) {
        showTranslation(message.translation);
        translatedCount += 1;
        showNotice("translation-notice", `Translating ${untranslatedCount} new articles… ${translatedCount} done`);
      } else {
        failures.push(message.failure);
      }
    }
    showNotice(
      "translation-notice",
      failures.length ? `${failures.length} articles weren't translated (${failures[0]}); they show in Hebrew and the next visit tries again.` : "",
    );
  } catch (error) {
    showNotice("translation-notice", `${error.message}. Showing the originals; the next visit tries again.`);
  }
}

const compactNumber = new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 });

function describeUsage(label, totals) {
  const dollars = totals.cost_dollars < 1 ? totals.cost_dollars.toFixed(3) : totals.cost_dollars.toFixed(2);
  return (
    `${label}: ${totals.articles_stored.toLocaleString("en")} stored · ${totals.articles_translated.toLocaleString("en")} translated · ` +
    `${compactNumber.format(totals.input_tokens)} in / ${compactNumber.format(totals.output_tokens)} out tokens · $${dollars}`
  );
}

// The grey totals in the top-right corner; a failure here just leaves the corner empty.
async function showUsage() {
  try {
    const response = await fetch("/usage");
    if (!response.ok) throw new Error(`the server answered ${response.status}`);
    const usage = await response.json();
    const monthName = new Date(`${usage.month}-01T00:00:00Z`).toLocaleString("en", { month: "long", timeZone: "UTC" });
    const lines = [describeUsage(monthName, usage.this_month), describeUsage("All time", usage.all_time)].map((text) => {
      const line = document.createElement("div");
      line.textContent = text;
      return line;
    });
    document.getElementById("usage").replaceChildren(...lines);
  } catch (error) {
    console.warn("Couldn't load usage totals", error);
  }
}

refreshFeeds()
  .then(() => Promise.all([...document.querySelectorAll(".column")].map(setUpColumn)))
  .then(async (firstPages) => {
    showUsage();
    await translateFirstPages(firstPages.flat());
    showUsage();
  });
