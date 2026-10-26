"""Datos del juego y de jugadores: fortnite-api.com (noticias del juego) y Fortnite Tracker (PR).

Fortnite Tracker exige clave (https://fortnitetracker.com/site-api) y permiso por escrito para
uso comercial. La ruta de power rankings se puede cambiar con TRN_PR_URL si la cambian ellos:
está sin comprobar contra una clave real.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from radio import db
from radio.config import env, user_agent
from radio.entities import Entity
from radio.models import KIND_DATA, KIND_OFFICIAL, Item

log = logging.getLogger(__name__)

TRN_PR_URL = "https://api.fortnitetracker.com/v1/powerrankings/pc/{region}/{name}"


def game_news() -> list[Item]:
    headers = {"User-Agent": user_agent()}
    if key := env("FORTNITE_API_KEY"):
        headers["Authorization"] = key
    try:
        r = httpx.get("https://fortnite-api.com/v2/news/br", params={"language": "es"}, headers=headers, timeout=30)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("fortnite-api.com falló: %s", exc)
        return []
    data = r.json().get("data") or {}
    date = data.get("date")
    published = datetime.fromisoformat(date.replace("Z", "+00:00")) if date else datetime.now(timezone.utc)
    return [
        Item(
            source="fortnite-api",
            kind=KIND_OFFICIAL,
            url=f"https://www.fortnite.com/news#{m.get('id', '')}",
            title=m.get("title", ""),
            text=m.get("body", ""),
            author="Epic Games",
            published_at=published,
            lang="es",
            media=[m["image"]] if m.get("image") else [],
        )
        for m in data.get("motds") or []
    ]


def _rank(payload: dict) -> int | None:
    for key in ("rank", "position", "globalRank"):
        if isinstance(payload.get(key), int):
            return payload[key]
    return None


def power_rankings(conn: sqlite3.Connection, players: list[Entity]) -> list[Item]:
    """Mira el PR de cada jugador con epic_name y avisa de subidas o bajadas fuertes."""
    key = env("TRN_API_KEY")
    if not key:
        return []
    template = env("TRN_PR_URL", TRN_PR_URL)
    now = datetime.now(timezone.utc)
    items = []
    with httpx.Client(headers={"TRN-Api-Key": key, "User-Agent": user_agent()}, timeout=30) as client:
        for p in players:
            if not p.epic_name:
                continue
            url = template.format(region=(p.region or "EU").lower(), name=quote(p.epic_name))
            try:
                r = client.get(url)
                r.raise_for_status()
                payload = r.json()
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                log.warning("Fortnite Tracker falló para %s: %s", p.name, exc)
                continue
            rank = _rank(payload)
            snap_key = f"tracker:pr:{p.id}"
            before = db.get_snapshot(conn, snap_key)
            db.set_snapshot(conn, snap_key, json.dumps({"rank": rank, "raw": payload})[:4000], now.isoformat())
            if rank is None or before is None:
                continue
            old = json.loads(before).get("rank")
            if old and abs(old - rank) >= max(10, old // 5):
                verb = "sube" if rank < old else "baja"
                items.append(
                    Item(
                        source="fortnite-tracker",
                        kind=KIND_DATA,
                        url=f"https://fortnitetracker.com/profile/all/{quote(p.epic_name)}/events",
                        title=f"{p.name} {verb} del puesto {old} al {rank} en el PR de {p.region or 'EU'}",
                        author="Fortnite Tracker",
                        published_at=now,
                        lang="es",
                    )
                )
    return items
