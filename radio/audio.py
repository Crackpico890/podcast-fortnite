"""Convierte un guion en un MP3 con dos voces, pausas y música.

Formato del guion (texto plano, una intervención por línea):

    [MUSICA intro]
    JAVI: Buenos días y bienvenidos...
    DANI: (ríe) Buenos días, Javi.
    [MUSICA cortinilla]

Las líneas vacías y las que empiezan por # se ignoran. Lo que va entre paréntesis
(acotaciones como "(ríe)") no se lee. [MUSICA nombre] mete data/music/<nombre>.mp3 si existe.

Voces: si hay GEMINI_API_KEY en .env, Gemini TTS con dos voces distintas que leen cada bloque
como una conversación (entiende las acotaciones: se ríe de verdad). Si no, o si Gemini falla,
edge-tts (gratis, sin clave), frase a frase. Se eligen con VOZ_<PRESENTADOR> (edge) y
VOZ_GEMINI_<PRESENTADOR> (Gemini). ffmpeg sale del paquete imageio-ffmpeg si no está instalado.
"""

import asyncio
import base64
import logging
import time
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
    raw: str = ""  # con acotaciones, para Gemini


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
                parts.append(Part(m.group(1), text, m.group(2).strip()))
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


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
GEMINI_VOICES = {"JAVI": "Charon", "DANI": "Fenrir"}
GEMINI_STYLE = (
    "Lee esta conversación de un programa de radio deportiva sobre Fortnite competitivo. "
    "Dos locutores hombres de España, con acento castellano peninsular, ritmo de radio, mucha energía "
    "y buen rollo, como dos amigos que se pican. Lo que va entre paréntesis indica cómo decirlo "
    "(por ejemplo, reírse) y no se lee en voz alta."
)


def gemini_block(lines: list[Part], out: Path) -> None:
    """Un bloque entero de diálogo en una sola llamada: así suena a conversación y no a frases sueltas."""
    import httpx

    names = sorted({p.speaker for p in lines})
    dialogue = "\n".join(f"{p.speaker.title()}: {p.raw}" for p in lines)
    body = {
        "contents": [{"parts": [{"text": f"{GEMINI_STYLE}\n\n{dialogue}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "multiSpeakerVoiceConfig": {
                    "speakerVoiceConfigs": [
                        {
                            "speaker": n.title(),
                            "voiceConfig": {
                                "prebuiltVoiceConfig": {
                                    "voiceName": env(f"VOZ_GEMINI_{n}") or GEMINI_VOICES.get(n, "Puck")
                                }
                            },
                        }
                        for n in names
                    ]
                }
            },
        },
    }
    url = GEMINI_URL.format(model=env("GEMINI_TTS_MODEL") or "gemini-2.5-flash-preview-tts")
    for attempt in range(3):
        r = httpx.post(url, params={"key": env("GEMINI_API_KEY")}, json=body, timeout=300)
        if r.status_code in (429, 500, 503) and attempt < 2:
            # El plan gratis tiene un límite por minuto: esperamos y reintentamos.
            time.sleep(30 * (attempt + 1))
            continue
        r.raise_for_status()
        break
    data = r.json()["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    pcm = out.with_suffix(".pcm")
    pcm.write_bytes(base64.b64decode(data))
    _run(["-f", "s16le", "-ar", "24000", "-ac", "1", "-i", pcm.as_posix(), out.as_posix()])


def blocks(parts: list[Part]) -> list[list[Part]]:
    """Agrupa el guion en bloques de diálogo separados por la música."""
    out: list[list[Part]] = []
    for p in parts:
        if not p.speaker:
            out.append([p])
        elif out and out[-1][0].speaker:
            out[-1].append(p)
        else:
            out.append([p])
    return out


def _edge_block(lines: list[Part], tmp: Path, n: int, silence: Path) -> list[Path]:
    pieces = []
    for i, p in enumerate(lines):
        mp3 = tmp / f"{n:04d}-{i:03d}.mp3"
        asyncio.run(_say(p.text, voice(p.speaker), *style(p.speaker), mp3))
        wav = tmp / f"{n:04d}-{i:03d}.wav"
        _run(["-i", mp3.as_posix(), "-ar", "24000", "-ac", "1", wav.as_posix()])
        pieces += [wav, silence]
    return pieces


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
        use_gemini = bool(env("GEMINI_API_KEY"))
        for n, block in enumerate(blocks(parts)):
            p = block[0]
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
            if use_gemini:
                wav = tmp / f"{n:04d}.wav"
                try:
                    gemini_block(block, wav)
                    pieces += [wav, silence]
                    continue
                except Exception as exc:  # noqa: BLE001 - si Gemini falla, el programa sale igual con edge-tts
                    log.warning("Gemini falló en el bloque %d (%s): uso edge-tts", n, exc)
            pieces += _edge_block(block, tmp, n, silence)
        listing = tmp / "list.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in pieces))
        out.parent.mkdir(parents=True, exist_ok=True)
        # Volumen como los podcasts (-16 LUFS) y MP3 de 128 kbps, lo que piden Spotify y compañía.
        _run(["-f", "concat", "-safe", "0", "-i", listing.as_posix(), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
              "-ar", "44100", "-ac", "2", "-b:a", "128k", out.as_posix()])
    return out
