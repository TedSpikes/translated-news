// Asks the server to fetch the feeds, then fills each column with articles from its category,
// newest first, and loads older ones on request: automatically near the bottom when the columns
// sit side by side, and with the "Load older articles" button when they are stacked (so one
// column can't block the next).

const ARTICLES_PER_PAGE = 30;
const HEBREW_LETTER = /[\u0590-\u05FF]/;
// Must match the width in style.css below which the columns stack.
const COLUMNS_SIDE_BY_SIDE = window.matchMedia("(min-width: 961px)");
const relativeTime = new Intl.RelativeTimeFormat("en", { numeric: "auto" });
const resultTemplate = document.getElementById("result-template");

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
  // Hebrew reads right to left; English (once translated) left to right.
  result.dir = HEBREW_LETTER.test(article.headline) ? "rtl" : "ltr";

  const title = result.querySelector(".result-title");
  title.href = url.href;
  title.textContent = article.headline;

  const favicon = result.querySelector(".result-favicon");
  favicon.src = article.site.favicon_url;
  favicon.addEventListener("error", () => (favicon.style.visibility = "hidden"));

  result.querySelector(".result-site-name").textContent = article.site.name;
  result.querySelector(".result-url").textContent = describeUrl(url);
  result.querySelector(".result-age").textContent = describeAge(article.published_at);

  const teaser = result.querySelector(".result-teaser");
  teaser.textContent = article.summary;
  teaser.hidden = !article.summary;
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
  let lastLoadFailed = false;

  function showStatus(message) {
    status.textContent = message;
    status.hidden = !message;
  }

  // The button stays hidden while a page loads, so neither a click nor the observer can start a second load.
  async function loadNextPage() {
    loadOlderButton.hidden = true;
    showStatus("Loading…");
    try {
      const response = await fetch(articlesUrl(column.dataset.category, lastShownArticle));
      if (!response.ok) throw new Error(`the server answered ${response.status}`);
      const articles = await response.json();
      resultList.append(...articles.map(renderArticle).filter(Boolean));
      lastLoadFailed = false;
      if (articles.length === ARTICLES_PER_PAGE) {
        lastShownArticle = articles.at(-1);
        showStatus("");
        loadOlderButton.hidden = false;
      } else if (lastShownArticle || articles.length) {
        showStatus("No older articles. The archive goes back to when the server started collecting.");
      } else {
        showStatus("No articles yet. The server fetches the feeds when it starts; reload in a few seconds.");
      }
    } catch (error) {
      // No automatic retry after a failure, or a down server would be asked again and again.
      lastLoadFailed = true;
      showStatus(`Couldn't load articles: ${error.message}`);
      loadOlderButton.hidden = false;
    }
  }

  // Fires when the button appears within 800px below the screen, including right after a page
  // loads, so a column keeps filling until its end is out of reach.
  const nearBottomObserver = new IntersectionObserver(
    ([entry]) => {
      if (entry.isIntersecting && COLUMNS_SIDE_BY_SIDE.matches && !lastLoadFailed) loadNextPage();
    },
    { rootMargin: "0px 0px 800px 0px" },
  );
  nearBottomObserver.observe(loadOlderButton);
  loadOlderButton.addEventListener("click", loadNextPage);
  loadNextPage();
}

// If the feeds can't be fetched, the saved articles are still worth showing, with a note saying so.
async function refreshFeeds() {
  try {
    const response = await fetch("/refresh", { method: "POST" });
    if (!response.ok) throw new Error(`the server answered ${response.status}`);
  } catch (error) {
    const notice = document.querySelector(".page-notice");
    notice.textContent = `Couldn't check for new articles (${error.message}); showing the saved ones.`;
    notice.hidden = false;
  }
}

refreshFeeds().then(() => document.querySelectorAll(".column").forEach(setUpColumn));
