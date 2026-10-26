# podcast-fortnite

Podcast diario (lunes a viernes) de Fortnite competitivo, España primero y todas las regiones en el punto de mira, hecho de forma automática.

Esta primera parte es el **motor de información**: recoge noticias las 24 horas, las une, decide si son oficiales, confirmadas o rumores, las ordena y te manda un resumen cada mañana por Telegram. El guion, las voces y la publicación vienen después.

## Cómo funciona

| Paso | Qué hace | Cuándo |
| --- | --- | --- |
| `python -m radio collect` | Google News (prensa ES y EN), Reddit, canales de YouTube, noticias del juego (fortnite-api.com) y PR de Fortnite Tracker | cada 30 min |
| `python -m radio liquipedia` | Lista de jugadores y equipos españoles de Liquipedia y aviso de cambios de equipo | 5:00 |
| `pc_agent/x_lists.py` | En tu PC, con una cuenta secundaria, lee tus listas de X despacio y lo manda al servidor | 3:00 |
| `python -m radio process` | Detecta jugadores y equipos, une la misma noticia de varias fuentes y la puntúa | tras cada recogida |
| `python -m radio digest --send` | Resumen por secciones (España, Torneos, Resto del mundo, Juego, Rumores) a Telegram | 6:30, L–V |

Cada historia lleva un nivel:

- **OFICIAL**: lo dice el protagonista, Epic o un dato (Liquipedia, Fortnite Tracker).
- **CONFIRMADO**: dos fuentes independientes, al menos una de ellas un medio.
- **PRENSA**: lo publica un medio, una sola fuente.
- **RUMOR**: redes, foros o textos que se presentan como rumor.

## Lista maestra

`data/seeds/espana.yaml` y `data/seeds/regiones.yaml` son las semillas a mano. Liquipedia completa el resto cada día. La ciudad de un jugador solo se guarda si él mismo la ha hecho pública.

## Instalar en el servidor (Contabo, Ubuntu o Debian)

```bash
ssh root@IP_DEL_SERVIDOR
git clone https://github.com/Crackpico890/podcast-fortnite /opt/podcast-fortnite
bash /opt/podcast-fortnite/deploy/install.sh
nano /opt/podcast-fortnite/.env   # Telegram, claves opcionales
```

Prueba a mano:

```bash
cd /opt/podcast-fortnite
sudo -u radio .venv/bin/python -m radio collect
sudo -u radio .venv/bin/python -m radio liquipedia
sudo -u radio .venv/bin/python -m radio process
sudo -u radio .venv/bin/python -m radio digest --send
```

## Lector de X en tu PC (Windows)

Va contra las normas de X: usa siempre una cuenta **secundaria** que puedas perder.

```powershell
git clone https://github.com/Crackpico890/podcast-fortnite
cd podcast-fortnite
pip install -r pc_agent/requirements.txt
python -m playwright install chromium
copy pc_agent\.env.example pc_agent\.env   # pon SERVER_URL e INGEST_TOKEN
python pc_agent/x_lists.py --login          # inicia sesión a mano una vez
powershell -ExecutionPolicy Bypass -File pc_agent\programar_windows.ps1
```

Añade las URLs de tus listas de X en `config/sources.yaml` (`x_lists`).

## Pruebas

```bash
pip install -r requirements.txt
python -m pytest
```

Fuentes de datos de Liquipedia bajo licencia CC BY-SA 3.0.
