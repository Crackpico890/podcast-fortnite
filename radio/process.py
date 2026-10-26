"""Procesa los items nuevos: entidades, historias, nivel, sección y puntuación."""

import sqlite3
from datetime import datetime, timedelta, timezone

from radio import db
from radio.entities import Matcher, load_all
from radio.pipeline import rank
from radio.pipeline.cluster import WINDOW, Cluster, assign


def _load_open_clusters(conn: sqlite3.Connection, matcher: Matcher, since: str) -> list[Cluster]:
    clusters: dict[int, Cluster] = {}
    rows = conn.execute(
        "SELECT * FROM items WHERE story_id IS NOT NULL AND published_at >= ? ORDER BY published_at", (since,)
    ).fetchall()
    for row in rows:
        it = db.row_to_item(row)
        c = clusters.setdefault(row["story_id"], Cluster(story_id=row["story_id"]))
        c.add(it, {e.id for e in matcher.find(it.body, it.author)})
    return list(clusters.values())


def run(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    matcher = Matcher(load_all(conn))
    by_id = {e.id: e for e in matcher.entities}
    since = (now - WINDOW - timedelta(hours=24)).isoformat()
    clusters = _load_open_clusters(conn, matcher, since)

    new_rows = conn.execute(
        "SELECT * FROM items WHERE story_id IS NULL AND published_at >= ? ORDER BY published_at", (since,)
    ).fetchall()
    touched: set[int] = set()
    for row in new_rows:
        it = db.row_to_item(row)
        found = matcher.find(it.body, it.author)
        for e in found:
            conn.execute("INSERT OR IGNORE INTO item_entities VALUES (?,?)", (row["id"], e.id))
        c = assign(it, {e.id for e in found}, clusters)
        if c.story_id is None:
            cur = conn.execute(
                "INSERT INTO stories (first_seen, last_seen, headline) VALUES (?,?,?)",
                (it.published_at.isoformat(), it.published_at.isoformat(), it.title),
            )
            c.story_id = cur.lastrowid
        conn.execute("UPDATE items SET story_id=? WHERE id=?", (c.story_id, row["id"]))
        touched.add(c.story_id)

    official = matcher.official_handles()
    for c in clusters:
        if c.story_id not in touched:
            continue
        entities = [by_id[i] for i in c.entity_ids if i in by_id]
        lvl = rank.level(c.items, official)
        reg = rank.region(entities)
        story_tags = set().union(*(rank.tags(i.body) for i in c.items))
        headline = max(c.items, key=lambda i: rank.KIND_WEIGHT.get(i.kind, 1.0)).title
        conn.execute(
            "UPDATE stories SET first_seen=?, last_seen=?, headline=?, level=?, score=?, section=? WHERE id=?",
            (
                c.first.isoformat(),
                c.last.isoformat(),
                headline,
                lvl,
                rank.score(c.items, lvl, reg, story_tags, now),
                rank.section(lvl, reg, story_tags, rank.relevant(c.items, entities)),
                c.story_id,
            ),
        )
    conn.commit()
    return len(new_rows)
