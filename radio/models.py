import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Cómo de fiable es el origen de un item, de más a menos.
KIND_OFFICIAL = "official"  # Epic, cuentas oficiales de equipos o jugadores
KIND_DATA = "data"  # Liquipedia, Fortnite Tracker: datos estructurados
KIND_PRESS = "press"  # prensa y webs de noticias
KIND_SOCIAL = "social"  # X, YouTube, Twitch
KIND_COMMUNITY = "community"  # Reddit, chivatazos


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def item_id(source: str, url: str, title: str = "") -> str:
    # La URL sola no basta: Liquipedia usa la misma página para cada cambio de equipo.
    return hashlib.sha1(f"{source}|{url}|{title}".encode()).hexdigest()[:20]


@dataclass
class Item:
    source: str
    kind: str
    url: str
    title: str
    text: str = ""
    author: str = ""
    published_at: datetime = field(default_factory=utcnow)
    lang: str = ""
    media: list[str] = field(default_factory=list)

    @property
    def id(self) -> str:
        return item_id(self.source, self.url, self.title if self.url else self.title + self.text)

    @property
    def body(self) -> str:
        return f"{self.title}\n{self.text}".strip()
