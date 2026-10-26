"""Lista maestra de jugadores, equipos, casters y creadores, y su detección en textos."""

import json
import re
import sqlite3
import unicodedata
from dataclasses import asdict, dataclass, field

from radio.config import load_yaml

SEED_FILES = ["data/seeds/espana.yaml", "data/seeds/regiones.yaml"]


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


@dataclass
class Entity:
    name: str
    kind: str = "player"  # player | team | org | caster | creator | desconocido
    aliases: list[str] = field(default_factory=list)
    country: str = ""  # código ISO: ES, FR, US...
    region: str = ""  # EU, NAC, NAW, BR, ASIA, ME, OCE
    team: str = ""
    # Solo si el propio jugador lo ha hecho público (bio, entrevistas, Liquipedia). Nunca deducirlo.
    city: str = ""
    x: str = ""
    youtube_channel_id: str = ""
    twitch: str = ""
    epic_name: str = ""
    liquipedia: str = ""
    # Alias que también son palabras normales ("locked", "KPI"): solo cuentan con la mayúscula exacta o como @usuario.
    ambiguous: bool = False
    verified: bool = False
    source: str = "seed"

    @property
    def id(self) -> str:
        return f"{self.kind}:{slug(self.name)}"

    @property
    def is_spanish(self) -> bool:
        return self.country.upper() == "ES"

    def all_aliases(self) -> list[str]:
        names = {self.name, *self.aliases}
        return sorted({a for a in names if a}, key=len, reverse=True)


def load_seeds() -> list[Entity]:
    entities = []
    for path in SEED_FILES:
        data = load_yaml(path)
        for raw in data.get("entidades", []) or []:
            entities.append(Entity(**raw))
    return entities


def save_entity(conn: sqlite3.Connection, entity: Entity) -> None:
    conn.execute(
        "INSERT INTO entities (id, kind, name, data) VALUES (?,?,?,?)"
        " ON CONFLICT(id) DO UPDATE SET data=excluded.data, name=excluded.name",
        (entity.id, entity.kind, entity.name, json.dumps(asdict(entity), ensure_ascii=False)),
    )


def load_all(conn: sqlite3.Connection) -> list[Entity]:
    """Semillas + lo descubierto automáticamente. Las semillas mandan si hay choque."""
    by_id = {}
    for row in conn.execute("SELECT data FROM entities"):
        e = Entity(**json.loads(row["data"]))
        by_id[e.id] = e
    for e in load_seeds():
        by_id[e.id] = e
    return list(by_id.values())


class Matcher:
    """Encuentra qué entidades aparecen en un texto."""

    def __init__(self, entities: list[Entity]):
        self.entities = entities
        self._patterns: list[tuple[re.Pattern, Entity]] = []
        for e in entities:
            handles = [h for h in (e.x, e.twitch) if h]
            for h in handles:
                self._patterns.append((re.compile(rf"@{re.escape(h.lstrip('@'))}\b", re.I), e))
            for alias in e.all_aliases():
                flags = 0 if e.ambiguous else re.I
                self._patterns.append((re.compile(rf"(?<![\w@]){re.escape(alias)}(?!\w)", flags), e))

    def find(self, text: str, author: str = "") -> list[Entity]:
        found = {}
        for pattern, e in self._patterns:
            if e.id not in found and pattern.search(text):
                found[e.id] = e
        author = author.lstrip("@").lower()
        if author:
            for e in self.entities:
                if author in {e.x.lstrip("@").lower(), e.twitch.lower()} - {""}:
                    found[e.id] = e
        return list(found.values())

    def official_handles(self) -> set[str]:
        return {e.x.lstrip("@").lower() for e in self.entities if e.x}
