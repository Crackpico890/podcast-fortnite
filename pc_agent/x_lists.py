"""Lector nocturno de listas de X. Se ejecuta en TU PC, con una cuenta SECUNDARIA.

Leer X así va contra sus normas: la cuenta puede ser suspendida. Por eso lee despacio,
como una persona, y nunca se usa con tu cuenta principal.

Primera vez (abre el navegador para que inicies sesión a mano):
    python pc_agent/x_lists.py --login
Cada noche (lo lanza el Programador de tareas de Windows):
    python pc_agent/x_lists.py
"""

import argparse
import json
import os
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
load_dotenv(HERE / ".env")

PROFILE = HERE / "x-profile"
OUT = HERE / "out"

EXTRACT_JS = """
() => [...document.querySelectorAll('article[data-testid="tweet"]')].map(a => {
  const t = a.querySelector('time');
  const link = t ? t.closest('a') : null;
  const text = a.querySelector('[data-testid="tweetText"]');
  const media = [...a.querySelectorAll('img[src*="pbs.twimg.com/media"]')].map(i => i.src);
  return {
    url: link ? link.href.split('?')[0] : '',
    author: link ? new URL(link.href).pathname.split('/')[1] : '',
    text: text ? text.innerText : '',
    published_at: t ? t.getAttribute('datetime') : '',
    media,
  };
})
"""


def human_pause(low: float = 2.5, high: float = 6.0) -> None:
    time.sleep(random.uniform(low, high))
    if random.random() < 0.08:  # de vez en cuando, una pausa larga como quien se distrae
        time.sleep(random.uniform(15, 45))


def read_list(page, url: str, cutoff: datetime, max_posts: int) -> list[dict]:
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_selector('article[data-testid="tweet"]', timeout=60_000)
    human_pause()
    posts: dict[str, dict] = {}
    stale_scrolls = 0
    while len(posts) < max_posts and stale_scrolls < 6:
        before = len(posts)
        oldest = None
        for p in page.evaluate(EXTRACT_JS):
            if not p["url"] or not p["published_at"]:
                continue
            when = datetime.fromisoformat(p["published_at"].replace("Z", "+00:00"))
            oldest = when if oldest is None else min(oldest, when)
            if when >= cutoff:
                posts.setdefault(p["url"], p)
        if oldest and oldest < cutoff:
            break
        stale_scrolls = stale_scrolls + 1 if len(posts) == before else 0
        page.mouse.wheel(0, random.randint(500, 1100))
        human_pause()
    return list(posts.values())


def send(posts: list[dict]) -> None:
    server, token = os.environ.get("SERVER_URL", "").rstrip("/"), os.environ.get("INGEST_TOKEN", "")
    OUT.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    (OUT / f"x-{stamp}.json").write_text(json.dumps(posts, ensure_ascii=False, indent=1), encoding="utf-8")
    if not server:
        print("Sin SERVER_URL: guardado solo en", OUT)
        return
    for i in range(0, len(posts), 200):
        r = httpx.post(
            f"{server}/ingest/x", json=posts[i : i + 200], headers={"Authorization": f"Bearer {token}"}, timeout=60
        )
        r.raise_for_status()
        print("servidor:", r.json())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--login", action="store_true", help="abrir X para iniciar sesión a mano")
    parser.add_argument("--hours", type=int, default=26, help="leer posts de las últimas N horas")
    parser.add_argument("--max-per-list", type=int, default=1500)
    args = parser.parse_args()

    from playwright.sync_api import sync_playwright

    lists = (yaml.safe_load((ROOT / "config/sources.yaml").read_text(encoding="utf-8")) or {}).get("x_lists") or []
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(str(PROFILE), headless=False, viewport={"width": 1280, "height": 900})
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        if args.login:
            page.goto("https://x.com/login")
            input("Inicia sesión con la cuenta SECUNDARIA y pulsa Enter aquí cuando veas tu inicio... ")
            ctx.close()
            return 0
        if not lists:
            print("No hay listas en config/sources.yaml (x_lists)")
            return 1
        cutoff = datetime.now(timezone.utc) - timedelta(hours=args.hours)
        everything = []
        for url in lists:
            try:
                got = read_list(page, url, cutoff, args.max_per_list)
                print(f"{url}: {len(got)} posts")
                everything += got
            except Exception as exc:  # una lista rota no debe parar las demás
                print(f"{url}: error {exc}", file=sys.stderr)
            human_pause(20, 60)
        ctx.close()
    send(everything)
    return 0


if __name__ == "__main__":
    sys.exit(main())
