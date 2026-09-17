#!/usr/bin/env bash
# One-shot provisioning for a fresh clone on the droplet. Idempotent --
# safe to re-run. Run as root from the repo root:
#
#   sudo bash deploy/setup_droplet.sh
#
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="meme"
SERVICE_PORT=8080

echo "== meme-pipeline droplet setup =="
echo "App dir: $APP_DIR"

if [ "$EUID" -ne 0 ]; then
  echo "Run as root: sudo bash deploy/setup_droplet.sh" >&2
  exit 1
fi

echo "-- installing system packages --"
apt-get update -y
apt-get install -y python3-venv python3-pip ffmpeg sqlite3 git

if ! id -u "$APP_USER" >/dev/null 2>&1; then
  echo "-- creating service user $APP_USER --"
  useradd --system --create-home --shell /usr/sbin/nologin "$APP_USER"
fi

chown -R "$APP_USER":"$APP_USER" "$APP_DIR"

echo "-- creating venv + installing python deps --"
sudo -u "$APP_USER" python3 -m venv "$APP_DIR/.venv"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install --upgrade pip
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

if [ ! -f "$APP_DIR/.env" ]; then
  echo "-- .env not found, seeding from .env.example (EDIT THIS before starting the dashboard) --"
  cp "$APP_DIR/.env.example" "$APP_DIR/.env"
fi
chown "$APP_USER":"$APP_USER" "$APP_DIR/.env"
chmod 600 "$APP_DIR/.env"

mkdir -p "$APP_DIR/secrets"
chown "$APP_USER":"$APP_USER" "$APP_DIR/secrets"
chmod 700 "$APP_DIR/secrets"

echo "-- generating synthesized sfx library --"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -m compiler.sfx_gen

echo "-- initializing database --"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c "import db; db.init_db()"

echo "-- installing systemd units --"
sed "s#__APP_DIR__#$APP_DIR#g; s#__APP_USER__#$APP_USER#g" \
  "$APP_DIR/deploy/meme-scrape.service.template" > /etc/systemd/system/meme-scrape.service
cp "$APP_DIR/deploy/meme-scrape.timer" /etc/systemd/system/meme-scrape.timer
sed "s#__APP_DIR__#$APP_DIR#g; s#__APP_USER__#$APP_USER#g; s#__PORT__#$SERVICE_PORT#g" \
  "$APP_DIR/deploy/meme-dashboard.service.template" > /etc/systemd/system/meme-dashboard.service

systemctl daemon-reload
systemctl enable --now meme-scrape.timer
systemctl enable --now meme-dashboard.service

cat <<EOF

== done ==
1. Edit $APP_DIR/.env with real API credentials (see .env.example), then:
     systemctl restart meme-dashboard
2. Generate a dashboard login: $APP_DIR/.venv/bin/python gen_password_hash.py
   -> paste DASHBOARD_PASSWORD_HASH into .env, then restart meme-dashboard
3. Tail scrape logs:     journalctl -u meme-scrape.service -f
4. Tail dashboard logs:  journalctl -u meme-dashboard.service -f
5. Trigger a scrape run right now (don't wait for the top of the hour):
     systemctl start meme-scrape.service
6. From your laptop:     ssh -L 8080:localhost:8080 <user>@159.223.116.60
                          then open http://localhost:8080

Optional disk-retention timer (deletes on-disk files, not DB rows, for
unused clips older than 14 days):
   sed "s#__APP_DIR__#$APP_DIR#g; s#__APP_USER__#$APP_USER#g" \\
     deploy/meme-retention.service.template > /etc/systemd/system/meme-retention.service
   cp deploy/meme-retention.timer /etc/systemd/system/meme-retention.timer
   systemctl daemon-reload && systemctl enable --now meme-retention.timer

Optional periodic auto-compile pass (compiler/auto_build.py -- only does
anything once the clip-selection autonomy dial in /settings is 'assisted'
or 'autonomous'; see autonomy.py):
   sed "s#__APP_DIR__#$APP_DIR#g; s#__APP_USER__#$APP_USER#g" \\
     deploy/meme-autobuild.service.template > /etc/systemd/system/meme-autobuild.service
   cp deploy/meme-autobuild.timer /etc/systemd/system/meme-autobuild.timer
   systemctl daemon-reload && systemctl enable --now meme-autobuild.timer

REQUIRED for the freeform tagging analyzer (analyzer/) to produce any
tags at all -- it calls a LOCAL vision model via Ollama, which this
script does NOT install (a CPU-only vision model is a heavy, optional
dependency; skip this if you don't want tagging/adaptive search params
yet -- clips still download fine without it, just untagged):
   curl -fsSL https://ollama.com/install.sh | sh
   ollama pull \${OLLAMA_VISION_MODEL:-llava:7b}
   # then set OLLAMA_HOST / OLLAMA_VISION_MODEL in .env if you're not
   # using the defaults (http://127.0.0.1:11434 / llava:7b).
   # NOTE: CPU-only inference on a small droplet is slow (can be tens of
   # seconds per clip) -- if that backs up the hourly scrape, consider a
   # bigger box or pointing OLLAMA_HOST at one, or swapping in a hosted
   # vision API in analyzer/vision.py later.
EOF
