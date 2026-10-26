"""Resumen diario de lo recogido, para revisarlo antes de que exista el guion."""

import html
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx

from radio.config import TIMEZONE, env

log = logging.getLogger(__name__)

SECTIONS = [
    ("espana", "España"),
    ("torneos", "Torneos"),
    ("mundo", "Resto del mundo"),
    ("juego", "Novedades del juego"),
    ("rumores", "Rumores (sin confirmar)"),
]
LEVEL_MARK = {"oficial": "OFICIAL", "confirmado": "CONFIRMADO", "prensa": "PRENSA", "rumor": "RUMOR"}
PER_SECTION = 12


def stories(conn: sqlite3.Connection, hours: int = 24, now: datetime | None = None) -> dict[str, list[dict]]:
    now = now or datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat()
    out: dict[str, list[dict]] = {key: [] for key, _ in SECTIONS}
    rows = conn.execute("SELECT * FROM stories WHERE last_seen >= ? ORDER BY score DESC", (since,)).fetchall()
    for s in rows:
        if len(out[s["section"]]) >= PER_SECTION:
            continue
        links = conn.execute(
            "SELECT url, author, source FROM items WHERE story_id=? ORDER BY published_at", (s["id"],)
        ).fetchall()
        out[s["section"]].append({**dict(s), "links": [dict(r) for r in links]})
    return out


def render(grouped: dict[str, list[dict]], now: datetime | None = None) -> str:
    """Texto en HTML simple para Telegram."""
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(TIMEZONE))
    lines = [f"<b>Resumen Fortnite competitivo · {now:%d/%m/%Y}</b>"]
    total = 0
    for key, title in SECTIONS:
        items = grouped.get(key) or []
        if not items:
            continue
        lines.append(f"\n<b>{title}</b>")
        for s in items:
            total += 1
            first = s["links"][0]["url"] if s["links"] else ""
            n = len({(l["author"] or l["source"]) for l in s["links"]})
            mark = LEVEL_MARK.get(s["level"], s["level"])
            head = html.escape(s["headline"])
            link = f' <a href="{html.escape(first)}">fuente</a>' if first else ""
            lines.append(f"• [{mark}] {head} ({n} fuente{'s' if n != 1 else ''}){link}")
    if total == 0:
        lines.append("\nNo hay nada nuevo en las últimas 24 horas. Revisa que las fuentes funcionan.")
    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    token, chat = env("TELEGRAM_BOT_TOKEN"), env("TELEGRAM_CHAT_ID")
    if not token or not chat:
        log.warning("Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID: no se envía nada")
        return False
    # Telegram corta en 4096 caracteres: mandamos por trozos en saltos de línea.
    chunk, ok = "", True
    for line in text.split("\n") + [None]:
        if line is None or len(chunk) + len(line) + 1 > 4000:
            if chunk:
                r = httpx.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat, "text": chunk, "parse_mode": "HTML", "disable_web_page_preview": True},
                    timeout=30,
                )
                ok &= r.status_code == 200
            chunk = ""
        if line is not None:
            chunk += line + "\n"
    return ok
