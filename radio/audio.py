"""Convierte un guion en un MP3 con dos voces, pausas y música.

Formato del guion (texto plano, una intervención por línea):

    [MUSICA intro]
    JAVI: Buenos días y bienvenidos...
    DANI: (ríe) Buenos días, Javi.
    [MUSICA cortinilla]

Las líneas vacías y las que empiezan por # se ignoran. Lo que va entre paréntesis
(acotaciones como "(ríe)") no se lee. [MUSICA nombre] mete data/music/<nombre>.mp3 si existe.

Voces: edge-tts (gratis, sin clave). Se eligen con VOZ_<PRESENTADOR> en .env, por ejemplo
VOZ_JAVI=es-ES-AlvaroNeural. ffmpeg sale del paquete imageio-ffmpeg si no está instalado.
"""

import asyncio
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from radio.config import env

log = logging.getLogger(__name__)

MUSIC_DIR = Path("data/music")
LINE_RE = re.compile(r"^([A-ZÁÉÍÓÚÑ]+):\s*(.+)$")
MUSIC_RE = re.compile(r"^\[MUSICA\s+([\w-]+)\]$", re.I)
STAGE_RE = re.compile(r"\([^)]*\)")

# Voces de España por orden de preferencia. Si la segunda no existe, se usa Álvaro con otro tono.
PREFERRED = {
    "JAVI": ["es-ES-AlvaroNeural"],
    "DANI": ["es-ES-TristanMultilingualNeural", "es-ES-AlvaroNeural"],
}
MUSIC_SECONDS = {"intro": 9, "cortinilla": 3, "outro": 10}
# Un poco más rápido que el habla por defecto, como en la radio deportiva. Cada uno con su tono.
STYLE = {"JAVI": ("+8%", "-2Hz"), "DANI": ("+12%", "+4Hz")}
PAUSE_SECONDS = 0.25


@dataclass
class Part:
    speaker: str  # "" para música
    text: str


def parse(script: str) -> list[Part]:
    parts = []
    for raw in script.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if m := MUSIC_RE.match(line):
            parts.append(Part("", m.group(1)))
        elif m := LINE_RE.match(line):
            text = re.sub(r"\s+", " ", STAGE_RE.sub("", m.group(2))).strip()
            if text:
                parts.append(Part(m.group(1), text))
    return parts


def ffmpeg() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


_available: set[str] | None = None


def voice(speaker: str) -> str:
    global _available
    if env(f"VOZ_{speaker}"):
        return env(f"VOZ_{speaker}")
    if _available is None:
        import edge_tts

        _available = {v["ShortName"] for v in asyncio.run(edge_tts.list_voices())}
    for name in PREFERRED.get(speaker, []):
        if name in _available:
            return name
    return "es-ES-AlvaroNeural"


def style(speaker: str) -> tuple[str, str]:
    rate, pitch = STYLE.get(speaker, ("+8%", "+0Hz"))
    # Si los dos acaban con la misma voz, que al menos no suenen igual.
    if speaker == "DANI" and voice("DANI") == voice("JAVI"):
        pitch = "+12Hz"
    return rate, pitch


async def _say(text: str, name: str, rate: str, pitch: str, out: Path) -> None:
    import edge_tts

    await edge_tts.Communicate(text, name, rate=rate, pitch=pitch).save(str(out))


def _run(args: list[str]) -> None:
    subprocess.run([ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def render(script: str, out: Path) -> Path:
    """Genera el MP3 final. Devuelve la ruta."""
    parts = parse(script)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        silence = tmp / "pause.wav"
        _run(["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", str(PAUSE_SECONDS), silence.as_posix()])
        pieces = []
        for n, p in enumerate(parts):
            if not p.speaker:
                music = MUSIC_DIR / f"{p.text}.mp3"
                if music.exists():
                    wav = tmp / f"{n:04d}.wav"
                    secs = MUSIC_SECONDS.get(p.text, 4)
                    fade = f"afade=t=in:d=0.3,afade=t=out:st={secs - 1.5}:d=1.5"
                    _run(["-i", music.as_posix(), "-ar", "24000", "-ac", "1", "-af", fade, "-t", str(secs), wav.as_posix()])
                    pieces.append(wav)
                else:
                    log.warning("Falta la música %s: se salta", music)
                continue
            mp3 = tmp / f"{n:04d}.mp3"
            asyncio.run(_say(p.text, voice(p.speaker), *style(p.speaker), mp3))
            wav = tmp / f"{n:04d}.wav"
            _run(["-i", mp3.as_posix(), "-ar", "24000", "-ac", "1", wav.as_posix()])
            pieces += [wav, silence]
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces))
        out.parent.mkdir(parents=True, exist_ok=True)
        # Volumen como los podcasts (-16 LUFS) y MP3 de 128 kbps, lo que piden Spotify y compañía.
        _run(["-f", "concat", "-safe", "0", "-i", listing.as_posix(), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
              "-ar", "44100", "-ac", "2", "-b:a", "128k", out.as_posix()])
    return out
