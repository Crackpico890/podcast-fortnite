"""Liquipedia Fortnite: jugadores españoles, sus equipos y cambios de equipo.

Normas de Liquipedia: User-Agent con contacto, gzip, como mucho 1 petición cada 2 segundos
y citar la fuente (CC BY-SA). Ver https://liquipedia.net/api-terms-of-use
"""

import logging
import re
import sqlite3
import time
from datetime import datetime, timezone

import httpx

from radio import db
from radio.config import user_agent
from radio.entities import Entity, save_entity
from radio.models import KIND_DATA, Item

log = logging.getLogger(__name__)

API = "https://liquipedia.net/fortnite/api.php"
WIKI = "https://liquipedia.net/fortnite/"
CATEGORIES = {"Category:Spanish_Players": "player", "Category:Spanish_Teams": "team"}
PAUSE_SECONDS = 2.1

FIELD_RE = re.compile(r"^\|\s*([a-z_0-9]+)\s*=\s*(.*?)\s*$", re.M)
LINK_RE = re.compile(r"\[\[(?:[^|\]]*\|)?([^\]]+)\]\]")


def parse_infobox(wikitext: str) -> dict[str, str]:
    """Saca los campos |clave=valor del infobox (primer bloque {{Infobox ...}})."""
    start = wikitext.find("{{Infobox")
    if start == -1:
        return {}
    end = wikitext.find("\n}}", start)
    block = wikitext[start : end if end != -1 else None]
    fields = {}
    for key, value in FIELD_RE.findall(block):
        value = LINK_RE.sub(r"\1", value).replace("{{", "").replace("}}", "").strip()
        if value:
            fields[key] = value
    return fields


def entity_from_page(title: str, kind: str, fields: dict[str, str]) -> Entity:
    return Entity(
        name=fields.get("id") or fields.get("name") or title,
        kind=kind,
        country="ES",
        region="EU",
        team=fields.get("team", ""),
        # Liquipedia solo publica la ubicación si es pública; la guardamos tal cual.
        city=fields.get("location", "") if kind == "player" else "",
        x=fields.get("twitter", ""),
        twitch=fields.get("twitch", ""),
        youtube_channel_id=fields.get("youtube", "") if fields.get("youtube", "").startswith("UC") else "",
        liquipedia=WIKI + title.replace(" ", "_"),
        verified=True,
        source="liquipedia",
    )


class Client:
    def __init__(self):
        self.http = httpx.Client(headers={"User-Agent": user_agent(), "Accept-Encoding": "gzip"}, timeout=30)
        self._last = 0.0

    def get(self, **params) -> dict:
        wait = PAUSE_SECONDS - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        params.setdefault("format", "json")
        r = self.http.get(API, params=params)
        self._last = time.monotonic()
        r.raise_for_status()
        return r.json()

    def category(self, title: str) -> list[str]:
        titles, cont = [], {}
        while True:
            data = self.get(action="query", list="categorymembers", cmtitle=title, cmlimit=500, **cont)
            titles += [m["title"] for m in data.get("query", {}).get("categorymembers", []) if m["ns"] == 0]
            if "continue" not in data:
                return titles
            cont = data["continue"]

    def wikitext(self, titles: list[str]) -> dict[str, str]:
        out = {}
        for i in range(0, len(titles), 50):
            data = self.get(
                action="query", prop="revisions", rvprop="content", rvslots="main", titles="|".join(titles[i : i + 50])
            )
            for page in data.get("query", {}).get("pages", {}).values():
                revs = page.get("revisions")
                if revs:
                    out[page["title"]] = revs[0]["slots"]["main"]["*"]
        return out


def sync(conn: sqlite3.Connection, client: Client | None = None) -> list[Item]:
    """Actualiza la lista de españoles y devuelve un item por cada cambio de equipo detectado."""
    client = client or Client()
    now = datetime.now(timezone.utc)
    items = []
    for category, kind in CATEGORIES.items():
        try:
            titles = client.category(category)
            pages = client.wikitext(titles)
        except httpx.HTTPError as exc:
            log.warning("Liquipedia falló en %s: %s", category, exc)
            continue
        for title, text in pages.items():
            entity = entity_from_page(title, kind, parse_infobox(text))
            save_entity(conn, entity)
            if kind != "player":
                continue
            key = f"liquipedia:team:{entity.id}"
            before = db.get_snapshot(conn, key)
            if before is not None and before != entity.team:
                items.append(team_change_item(entity, before, now))
            db.set_snapshot(conn, key, entity.team, now.isoformat())
    conn.commit()
    return items


def team_change_item(entity: Entity, before: str, now: datetime) -> Item:
    if entity.team and before:
        title = f"{entity.name} deja {before} y ahora figura en {entity.team}"
    elif entity.team:
        title = f"{entity.name} ficha por {entity.team}"
    else:
        title = f"{entity.name} deja {before} y queda sin equipo"
    return Item(
        source="liquipedia",
        kind=KIND_DATA,
        url=entity.liquipedia,
        title=title,
        text="Cambio de equipo detectado en Liquipedia.",
        author="Liquipedia",
        published_at=now,
        lang="es",
    )
