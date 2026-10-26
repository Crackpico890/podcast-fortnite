"""Uso: python -m radio <comando>

  collect     recoge de todas las fuentes gratuitas (cada 30 min)
  liquipedia  sincroniza españoles de Liquipedia y detecta cambios de equipo y de región (1 vez al día)
  process     une duplicados, asigna nivel, sección y puntuación (--dias-atras N para rehacer más días)
  digest      imprime el resumen de ayer (el lunes, del fin de semana) y lo más importante de la semana;
              --dia AAAA-MM-DD para otro día, --send lo manda por Telegram
  serve       arranca la API que recibe los posts de X desde tu PC
  audio       convierte un guion en MP3: audio episodios/2026-10-26.txt [--motor eleven|gemini|edge] [--bloques 2]
  voces       lista voces masculinas de España de ElevenLabs (id, nombre, acento, muestra)
"""

import argparse
import logging
import sys
from datetime import date

from radio import db, digest, process
from radio.collectors import feeds, game, liquipedia
from radio.config import env, sources
from radio.entities import load_all


def cmd_collect(conn) -> None:
    entities = load_all(conn)
    youtube = [(e.name, e.youtube_channel_id) for e in entities if e.youtube_channel_id]
    items = feeds.collect(sources(), youtube) + game.game_news()
    items += game.power_rankings(conn, [e for e in entities if e.kind == "player"])
    print(f"recogidos {len(items)}, nuevos {db.save_items(conn, items)}")


def cmd_liquipedia(conn) -> None:
    items = liquipedia.sync(conn)
    print(f"cambios de equipo o región detectados: {len(items)}, nuevos {db.save_items(conn, items)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="radio", description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("command", choices=["collect", "liquipedia", "process", "digest", "serve", "audio", "voces"])
    parser.add_argument("guion", nargs="?", help="audio: archivo del guion")
    parser.add_argument("--send", action="store_true", help="digest: enviar por Telegram")
    parser.add_argument("--dia", type=date.fromisoformat, help="digest: día a resumir (por defecto, ayer)")
    parser.add_argument("--dias-atras", type=int, help="process: rehacer los últimos N días (por defecto 3)")
    parser.add_argument("--motor", choices=["eleven", "gemini", "edge"], help="audio: forzar un motor de voz")
    parser.add_argument("--bloques", type=int, help="audio: solo los N primeros bloques de diálogo (muestra)")
    parser.add_argument("--port", type=int, default=int(env("INGEST_PORT") or 8787), help="serve: puerto")
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
            print(f"procesados {process.run(conn, days=args.dias_atras)} items nuevos")
        elif args.command == "audio":
            from pathlib import Path

            from radio import audio

            src = Path(args.guion)
            name = src.stem + (f"-{args.motor}" if args.motor else "") + ("-muestra" if args.bloques else "")
            print(audio.render(src.read_text(encoding="utf-8"), Path("data/episodios") / f"{name}.mp3",
                               engine=args.motor, max_blocks=args.bloques))
        elif args.command == "voces":
            import httpx

            from radio import audio

            if not env("ELEVENLABS_API_KEY"):
                print("Falta ELEVENLABS_API_KEY en .env")
                return 1
            try:
                voices = audio.spanish_voices()
            except httpx.HTTPStatusError as exc:
                print(f"ElevenLabs respondió {exc.response.status_code}: {exc.response.text[:300]}")
                return 1
            for v in voices:
                print(v["voice_id"], "|", v.get("name"), "|", v.get("accent"), "|", v.get("age"), "|",
                      v.get("descriptive"), "|", v.get("preview_url"))
        elif args.command == "digest":
            first, last = (args.dia, args.dia) if args.dia else digest.covered_days()
            grouped = digest.stories(conn, first, last)
            weekly = digest.week(conn, first, grouped)
            text = digest.render(grouped, first, last, weekly, digest.abroad(load_all(conn)))
            print(text)
            if args.send and not digest.send_telegram(text):
                return 1
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
