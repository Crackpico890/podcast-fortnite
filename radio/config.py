import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

TIMEZONE = "Europe/Madrid"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def db_path() -> Path:
    path = Path(env("RADIO_DB", "data/radio.sqlite3"))
    return path if path.is_absolute() else ROOT / path


def load_yaml(relative: str) -> dict:
    path = ROOT / relative
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def sources() -> dict:
    return load_yaml("config/sources.yaml")


def user_agent() -> str:
    contact = env("LIQUIPEDIA_CONTACT", "sin-contacto")
    return f"PodcastFortniteBot/0.1 (+https://github.com/Crackpico890/podcast-fortnite; {contact})"
