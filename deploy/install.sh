#!/usr/bin/env bash
# Instala el motor de información en el VPS (Ubuntu o Debian).
# Convive con otros programas del servidor: va en su carpeta, con su propio usuario y su propio
# Python, no cambia la hora del sistema y tiene límites de CPU y memoria.
# Ejecutar como root:
#   git clone https://github.com/Crackpico890/podcast-fortnite /opt/podcast-fortnite
#   bash /opt/podcast-fortnite/deploy/install.sh
set -euo pipefail

APP=/opt/podcast-fortnite
cd "$APP"

apt-get update -y
apt-get install -y python3 python3-venv python3-pip git tzdata cron

id -u radio >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin radio
python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  token=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')
  sed -i "s|^INGEST_TOKEN=.*|INGEST_TOKEN=$token|" .env
  echo "Creado .env con un INGEST_TOKEN aleatorio. Rellena el resto: nano $APP/.env"
fi

# Puerto de la API que recibe los posts de X: el que ya esté en .env o el primero libre desde 8787,
# para no chocar con otros programas del servidor.
port=$(grep -E '^INGEST_PORT=[0-9]+$' .env | cut -d= -f2 || true)
if [ -z "$port" ]; then
  used=$(ss -ltnH | awk '{print $4}')
  for candidate in $(seq 8787 8899); do
    if ! grep -qE ":$candidate\$" <<<"$used"; then port=$candidate; break; fi
  done
  [ -n "$port" ] || { echo "No hay ningún puerto libre entre 8787 y 8899" >&2; exit 1; }
  grep -q '^INGEST_PORT=' .env && sed -i "s|^INGEST_PORT=.*|INGEST_PORT=$port|" .env || echo "INGEST_PORT=$port" >> .env
fi
mkdir -p data
chown -R radio:radio "$APP"

install -m 644 deploy/radio-ingest.service /etc/systemd/system/radio-ingest.service
systemctl daemon-reload
systemctl enable --now radio-ingest

install -m 644 deploy/crontab /etc/cron.d/podcast-fortnite
systemctl restart cron || true

ip=$(hostname -I | awk '{print $1}')
echo "Listo. La API para el lector de X escucha en http://$ip:$port"
echo "Prueba: sudo -u radio $APP/.venv/bin/python -m radio collect"
