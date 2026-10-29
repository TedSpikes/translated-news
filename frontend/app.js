// Fills each column on the page with the newest articles from its category.

const ARTICLES_PER_COLUMN = 30;
const HEBREW_LETTER = /[\u0590-\u05FF]/;
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

function statusItem(message) {
  const item = document.createElement("li");
  item.className = "status";
  item.textContent = message;
  return item;
}

async function fillColumn(column) {
  const resultList = column.querySelector(".results");
  const query = new URLSearchParams({ category: column.dataset.category, limit: ARTICLES_PER_COLUMN });
  try {
    const response = await fetch(`/articles?${query}`);
    if (!response.ok) throw new Error(`the server answered ${response.status}`);
    const results = (await response.json()).map(renderArticle).filter(Boolean);
    if (results.length) {
      resultList.replaceChildren(...results);
    } else {
      resultList.replaceChildren(statusItem("No articles yet. The server fetches the feeds when it starts; reload in a few seconds."));
    }
  } catch (error) {
    resultList.replaceChildren(statusItem(`Couldn't load articles: ${error.message}`));
  }
}

document.querySelectorAll(".column").forEach(fillColumn);
