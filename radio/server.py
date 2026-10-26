"""API mínima para que el lector de X de tu PC mande los posts al servidor."""

import hmac
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from radio import db
from radio.config import env
from radio.models import KIND_SOCIAL, Item

app = FastAPI(title="podcast-fortnite ingest")


class Post(BaseModel):
    url: str
    author: str
    text: str
    published_at: datetime
    media: list[str] = []


def check_token(authorization: str = Header(default="")) -> None:
    expected = env("INGEST_TOKEN")
    given = authorization.removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given, expected):
        raise HTTPException(status_code=401, detail="token incorrecto")


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.post("/ingest/x", dependencies=[Depends(check_token)])
def ingest_x(posts: list[Post]) -> dict:
    items = [
        Item(
            source="x",
            kind=KIND_SOCIAL,
            url=p.url,
            title=p.text.split("\n")[0][:280],
            text=p.text,
            author=p.author.lstrip("@"),
            published_at=p.published_at if p.published_at.tzinfo else p.published_at.replace(tzinfo=timezone.utc),
            media=p.media,
        )
        for p in posts
    ]
    conn = db.connect()
    try:
        new = db.save_items(conn, items)
    finally:
        conn.close()
    return {"received": len(items), "new": new}
