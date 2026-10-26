"""Uso: python -m radio <comando>

  collect     recoge de todas las fuentes gratuitas (cada 30 min)
  liquipedia  sincroniza españoles de Liquipedia y detecta cambios de equipo (1 vez al día)
  process     une duplicados, asigna nivel, sección y puntuación
  digest      imprime el resumen de las últimas 24 h (--send lo manda por Telegram)
  serve       arranca la API que recibe los posts de X desde tu PC
"""

import argparse
import logging
import sys

from radio import db, digest, process
from radio.collectors import feeds, game, liquipedia
from radio.config import sources
from radio.entities import load_all


def cmd_collect(conn) -> None:
    entities = load_all(conn)
    youtube = [(e.name, e.youtube_channel_id) for e in entities if e.youtube_channel_id]
    items = feeds.collect(sources(), youtube) + game.game_news()
    items += game.power_rankings(conn, [e for e in entities if e.kind == "player"])
    print(f"recogidos {len(items)}, nuevos {db.save_items(conn, items)}")


def cmd_liquipedia(conn) -> None:
    items = liquipedia.sync(conn)
    print(f"cambios de equipo detectados: {len(items)}, nuevos {db.save_items(conn, items)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radio", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("command", choices=["collect", "liquipedia", "process", "digest", "serve"])
    parser.add_argument("--send", action="store_true", help="digest: enviar por Telegram")
    parser.add_argument("--port", type=int, default=8787, help="serve: puerto")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.command == "serve":
        import uvicorn

        uvicorn.run("radio.server:app", host="0.0.0.0", port=args.port)
        return 0

    conn = db.connect()
    try:
        if args.command == "collect":
            cmd_collect(conn)
        elif args.command == "liquipedia":
            cmd_liquipedia(conn)
        elif args.command == "process":
            print(f"procesados {process.run(conn)} items nuevos")
        elif args.command == "digest":
            text = digest.render(digest.stories(conn))
            print(text)
            if args.send and not digest.send_telegram(text):
                return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
