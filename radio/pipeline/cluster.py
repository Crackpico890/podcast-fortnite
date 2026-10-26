"""Une en una sola historia la misma noticia contada por varias fuentes."""

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from radio.models import Item

WINDOW = timedelta(hours=48)

STOPWORDS = set(
    """
    a al ante con de del desde el en entre es esta este ha han la las lo los mas para pero por que se sin
    su sus un una y o ya the an and are as at be by for from has have in is it its of on or that this to
    was were will with after new fortnite via rt
    """.split()
)


def tokens(text: str) -> set[str]:
    text = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    text = re.sub(r"https?://\S+", " ", text)
    return {w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2 and w not in STOPWORDS}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


@dataclass
class Cluster:
    items: list[Item] = field(default_factory=list)
    entity_ids: set[str] = field(default_factory=set)
    words: set[str] = field(default_factory=set)
    first: datetime | None = None
    last: datetime | None = None
    story_id: int | None = None

    def add(self, item: Item, entity_ids: set[str]) -> None:
        self.items.append(item)
        self.entity_ids |= entity_ids
        self.words |= tokens(item.title)
        self.first = min(filter(None, [self.first, item.published_at]))
        self.last = max(filter(None, [self.last, item.published_at]))

    def similarity(self, item: Item, entity_ids: set[str]) -> float:
        score = jaccard(self.words, tokens(item.title))
        if entity_ids & self.entity_ids:
            score += 0.25
        return score


THRESHOLD = 0.45


def assign(item: Item, entity_ids: set[str], clusters: list[Cluster]) -> Cluster:
    """Mete el item en la historia más parecida reciente, o abre una nueva."""
    best, best_score = None, 0.0
    for c in clusters:
        if c.last and abs(item.published_at - c.last) > WINDOW:
            continue
        s = c.similarity(item, entity_ids)
        if s > best_score:
            best, best_score = c, s
    if best is None or best_score < THRESHOLD:
        best = Cluster()
        clusters.append(best)
    best.add(item, entity_ids)
    return best
