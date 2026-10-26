import json
import sqlite3
from datetime import datetime
from pathlib import Path

from radio.config import db_path
from radio.models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    kind TEXT NOT NULL,
    url TEXT,
    title TEXT,
    text TEXT,
    author TEXT,
    published_at TEXT NOT NULL,
    lang TEXT,
    media TEXT,
    fetched_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    story_id INTEGER
);
CREATE INDEX IF NOT EXISTS items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS items_story ON items(story_id);

CREATE TABLE IF NOT EXISTS entities (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    data TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS item_entities (
    item_id TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    PRIMARY KEY (item_id, entity_id)
);

CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    headline TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'rumor',
    score REAL NOT NULL DEFAULT 0,
    section TEXT NOT NULL DEFAULT 'mundo',
    told_on TEXT
);

-- Fotos de datos que cambian (equipo en Liquipedia, PR en Tracker) para detectar novedades.
CREATE TABLE IF NOT EXISTS snapshots (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or db_path()
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def save_items(conn: sqlite3.Connection, items: list[Item]) -> int:
    """Guarda items nuevos y devuelve cuántos no existían ya."""
    new = 0
    for it in items:
        cur = conn.execute(
            "INSERT OR IGNORE INTO items (id, source, kind, url, title, text, author, published_at, lang, media)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                it.id,
                it.source,
                it.kind,
                it.url,
                it.title,
                it.text,
                it.author,
                it.published_at.isoformat(),
                it.lang,
                json.dumps(it.media),
            ),
        )
        new += cur.rowcount
    conn.commit()
    return new


def row_to_item(row: sqlite3.Row) -> Item:
    return Item(
        source=row["source"],
        kind=row["kind"],
        url=row["url"] or "",
        title=row["title"] or "",
        text=row["text"] or "",
        author=row["author"] or "",
        published_at=datetime.fromisoformat(row["published_at"]),
        lang=row["lang"] or "",
        media=json.loads(row["media"] or "[]"),
    )


def get_snapshot(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM snapshots WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_snapshot(conn: sqlite3.Connection, key: str, value: str, now: str) -> None:
    conn.execute(
        "INSERT INTO snapshots (key, value, updated_at) VALUES (?,?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, value, now),
    )
    conn.commit()
