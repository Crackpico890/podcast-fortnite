"""Fuentes por RSS: Google News, Reddit, YouTube y webs con feed."""

import calendar
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import httpx

from radio.config import user_agent
from radio.models import KIND_COMMUNITY, KIND_PRESS, KIND_SOCIAL, Item

log = logging.getLogger(__name__)
TAG_RE = re.compile(r"<[^>]+>")


def _clean(html: str) -> str:
    return re.sub(r"\s+", " ", TAG_RE.sub(" ", html or "")).strip()


def _published(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key)
        if value:
            return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)
    return datetime.now(timezone.utc)


def parse_feed(content: str | bytes, source: str, kind: str, lang: str = "") -> list[Item]:
    feed = feedparser.parse(content)
    items = []
    for e in feed.entries:
        author = e.get("author", "")
        # Google News pone el medio en <source>; lo usamos como autor para contar fuentes distintas.
        if e.get("source") and e.source.get("title"):
            author = e.source["title"]
        items.append(
            Item(
                source=source,
                kind=kind,
                url=e.get("link", ""),
                title=_clean(e.get("title", "")),
                text=_clean(e.get("summary", ""))[:2000],
                author=author,
                published_at=_published(e),
                lang=lang,
            )
        )
    return items


REDDIT_PAUSE_SECONDS = 6


def fetch(url: str, client: httpx.Client, retry_after_429: bool = False) -> bytes | None:
    try:
        r = client.get(url, timeout=30, follow_redirects=True)
        if r.status_code == 429 and retry_after_429:
            # Reddit limita las peticiones seguidas: esperamos lo que pide (máx. 60 s) y probamos una vez más.
            wait = r.headers.get("retry-after", "")
            time.sleep(min(int(wait) if wait.isdigit() else 10, 60))
            r = client.get(url, timeout=30, follow_redirects=True)
        r.raise_for_status()
        return r.content
    except httpx.HTTPError as exc:
        log.warning("No se pudo leer %s: %s", url, exc)
        return None


def google_news_url(q: str, hl: str = "es", gl: str = "ES") -> str:
    return f"https://news.google.com/rss/search?q={quote_plus(q)}&hl={hl}&gl={gl}&ceid={gl}:{hl}"


def collect(conf: dict, youtube_ids: list[tuple[str, str]] = ()) -> list[Item]:
    items: list[Item] = []
    with httpx.Client(headers={"User-Agent": user_agent()}) as client:
        for g in conf.get("google_news", []) or []:
            url = google_news_url(g["q"], g.get("hl", "es"), g.get("gl", "ES"))
            if data := fetch(url, client):
                items += parse_feed(data, "google-news", KIND_PRESS, g.get("hl", ""))

        for i, sub in enumerate(conf.get("reddit", []) or []):
            if i:
                time.sleep(REDDIT_PAUSE_SECONDS)
            if data := fetch(f"https://www.reddit.com/r/{sub}/new/.rss", client, retry_after_429=True):
                items += parse_feed(data, f"reddit:{sub}", KIND_COMMUNITY, "en")

        for feed in conf.get("rss", []) or []:
            if data := fetch(feed["url"], client):
                items += parse_feed(data, f"rss:{feed['name']}", feed.get("kind", KIND_PRESS))

        channels = [(c["name"], c["channel_id"]) for c in conf.get("youtube_channels", []) or []]
        for name, channel_id in [*channels, *youtube_ids]:
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            if data := fetch(url, client):
                for it in parse_feed(data, "youtube", KIND_SOCIAL):
                    it.author = it.author or name
                    items.append(it)
    return items
