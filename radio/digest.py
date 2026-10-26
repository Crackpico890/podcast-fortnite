"""Resumen diario de lo recogido, para revisarlo antes de que exista el guion."""

import html
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
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


# El episodio de un día cuenta lo que pasó el día anterior; el del lunes, todo el fin de semana
# (viernes, sábado y domingo), que es cuando se juegan los torneos grandes. Cada "día" va de
# 6:00 a 6:00 de Madrid para que entren los torneos de NA, que acaban de madrugada en España.
DAY_STARTS_AT_HOUR = 6
WEEK_HIGHLIGHTS = 6


def window(first: date, last: date) -> tuple[datetime, datetime]:
    tz = ZoneInfo(TIMEZONE)
    start = datetime.combine(first, time(DAY_STARTS_AT_HOUR), tzinfo=tz)
    end = datetime.combine(last + timedelta(days=1), time(DAY_STARTS_AT_HOUR), tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def day_window(day: date) -> tuple[datetime, datetime]:
    return window(day, day)


def yesterday(now: datetime | None = None) -> date:
    now = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(TIMEZONE))
    return (now - timedelta(hours=DAY_STARTS_AT_HOUR)).date() - timedelta(days=1)


def covered_days(now: datetime | None = None) -> tuple[date, date]:
    """Días que cuenta el episodio de hoy: ayer, o de viernes a domingo si hoy es lunes."""
    last = yesterday(now)
    first = last - timedelta(days=2) if last.weekday() == 6 else last
    return first, last


def _with_links(conn: sqlite3.Connection, s: sqlite3.Row) -> dict:
    links = conn.execute(
        "SELECT url, author, source FROM items WHERE story_id=? ORDER BY published_at", (s["id"],)
    ).fetchall()
    return {**dict(s), "links": [dict(r) for r in links]}


def stories(conn: sqlite3.Connection, first: date, last: date | None = None) -> dict[str, list[dict]]:
    start, end = window(first, last or first)
    rows = conn.execute(
        "SELECT * FROM stories WHERE last_seen >= ? AND first_seen < ? ORDER BY score DESC",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    out: dict[str, list[dict]] = {key: [] for key, _ in SECTIONS}
    for s in rows:
        if s["section"] not in out or len(out[s["section"]]) >= PER_SECTION:
            continue
        out[s["section"]].append(_with_links(conn, s))
    return out


def week(conn: sqlite3.Connection, first: date, already: dict[str, list[dict]]) -> list[dict]:
    """Lo más importante de los torneos (y de España) de los 7 días anteriores que no se cuenta ya."""
    end, _ = window(first, first)
    start = end - timedelta(days=7)
    told = {s["id"] for group in already.values() for s in group}
    rows = conn.execute(
        "SELECT * FROM stories WHERE last_seen >= ? AND last_seen < ? AND section IN ('torneos', 'espana')"
        " ORDER BY score DESC",
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    return [_with_links(conn, s) for s in rows if s["id"] not in told][:WEEK_HIGHLIGHTS]


def _line(s: dict) -> str:
    first = s["links"][0]["url"] if s["links"] else ""
    n = len({(l["author"] or l["source"]) for l in s["links"]})
    mark = LEVEL_MARK.get(s["level"], s["level"])
    link = f' <a href="{html.escape(first)}">fuente</a>' if first else ""
    return f"• [{mark}] {html.escape(s['headline'])} ({n} fuente{'s' if n != 1 else ''}){link}"


def render(grouped: dict[str, list[dict]], first: date, last: date | None = None, weekly: list[dict] = ()) -> str:
    """Texto en HTML simple para Telegram."""
    last = last or first
    when = f"el {first:%d/%m/%Y}" if first == last else f"del {first:%d/%m} al {last:%d/%m/%Y}"
    lines = [f"<b>Resumen Fortnite competitivo · lo que pasó {when}</b>"]
    total = 0
    for key, title in SECTIONS:
        items = grouped.get(key) or []
        if not items:
            continue
        lines.append(f"\n<b>{title}</b>")
        for s in items:
            total += 1
            lines.append(_line(s))
    if total == 0:
        lines.append("\nNo hay nada de competitivo esos días. Revisa que las fuentes funcionan.")
    if weekly:
        lines.append("\n<b>Lo más importante de la semana en torneos</b>")
        lines += [_line(s) for s in weekly]
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
