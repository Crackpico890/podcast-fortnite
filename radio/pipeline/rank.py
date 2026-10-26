"""Nivel de confirmación, sección del programa y puntuación de cada historia."""

import math
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from radio.entities import Entity
from radio.models import KIND_COMMUNITY, KIND_DATA, KIND_OFFICIAL, KIND_PRESS, KIND_SOCIAL, Item

# oficial: lo dice el protagonista, Epic o un dato (Liquipedia, Tracker)
# confirmado: dos fuentes independientes
# prensa: lo publica un medio, una sola fuente
# rumor: redes, foros o palabras de rumor, sin confirmar
OFICIAL, CONFIRMADO, PRENSA, RUMOR = "oficial", "confirmado", "prensa", "rumor"

KIND_WEIGHT = {KIND_OFFICIAL: 1.5, KIND_DATA: 1.3, KIND_PRESS: 1.2, KIND_SOCIAL: 1.0, KIND_COMMUNITY: 0.8}
REGION_WEIGHT = {"ES": 3.0, "EU": 1.5}
LEVEL_WEIGHT = {OFICIAL: 1.3, CONFIRMADO: 1.15, PRENSA: 1.05, RUMOR: 1.0}

KEYWORDS = {
    "fichaje": (2.0, r"fich(a|aje|ado)|se une|nuevo equipo|joins?|signs?|signed|welcome|bienvenid|anuncia"),
    "baja": (1.8, r"deja\b|abandona|leaves?|left|parts? ways|free agent|agente libre|se separa|released"),
    "polemica": (1.8, r"polem|drama|banead|banned|ban\b|cheat|chet|tramp|descalific|disqualif|sanci"),
    "victoria": (1.6, r"gana|ganan|wins?|won|campe[oó]n|champion|victoria|1st|primer puesto"),
    "torneo": (1.3, r"fncs|major|global championship|lan\b|cash cup|eval cup|final|torneo|tournament|qualif|clasific"),
    "parche": (1.2, r"parche|patch|update|actualizaci|hotfix|nerf|buff|competitive rules|reglas"),
    "rumor": (0.0, r"rumor|rumour|se dice|insider|leak|filtra|sources say|fuentes"),
}
COMPILED = {k: (w, re.compile(p, re.I)) for k, (w, p) in KEYWORDS.items()}


def tags(text: str) -> set[str]:
    return {k for k, (_, rx) in COMPILED.items() if rx.search(text)}


def independent_sources(items: list[Item]) -> int:
    seen = set()
    for it in items:
        who = (it.author or urlparse(it.url).netloc or it.source).lower()
        seen.add(who)
    return len(seen)


def level(items: list[Item], official_handles: set[str]) -> str:
    for it in items:
        if it.kind in (KIND_OFFICIAL, KIND_DATA):
            return OFICIAL
        if it.source == "x" and it.author.lstrip("@").lower() in official_handles:
            return OFICIAL
    if all("rumor" in tags(i.body) for i in items):
        return RUMOR
    # Dos usuarios de Reddit repitiendo lo mismo no confirman nada: hace falta al menos un medio.
    if independent_sources(items) >= 2 and any(i.kind == KIND_PRESS for i in items):
        return CONFIRMADO
    if any(i.kind == KIND_PRESS for i in items):
        return PRENSA
    return RUMOR


def region(entities: list[Entity]) -> str:
    if any(e.is_spanish for e in entities):
        return "ES"
    regions = [e.region for e in entities if e.region]
    return regions[0] if regions else ""


def section(lvl: str, reg: str, story_tags: set[str]) -> str:
    if lvl == RUMOR:
        return "rumores"
    if reg == "ES":
        return "espana"
    if "parche" in story_tags and not story_tags & {"fichaje", "baja", "victoria"}:
        return "juego"
    if story_tags & {"torneo", "victoria"}:
        return "torneos"
    return "mundo"


def score(items: list[Item], lvl: str, reg: str, story_tags: set[str], now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    base = max(KIND_WEIGHT.get(i.kind, 1.0) for i in items)
    topic = max([COMPILED[t][0] for t in story_tags] or [1.0]) or 1.0
    spread = 1 + math.log2(independent_sources(items))
    newest = max(i.published_at for i in items)
    age_h = max(0.0, (now - newest).total_seconds() / 3600)
    freshness = 0.5 ** (age_h / 24)  # cada día que pasa vale la mitad
    return round(base * topic * spread * REGION_WEIGHT.get(reg, 1.0) * LEVEL_WEIGHT[lvl] * freshness, 3)
