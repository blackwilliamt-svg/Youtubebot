#!/usr/bin/env bash
# Pull latest code + reinstall deps + restart the dashboard. Run as root
# (or a user with sudo) from the repo root on the droplet:
#
#   sudo bash deploy/deploy.sh
#
# The scrape timer doesn't need a restart for ordinary code changes since
# it starts a fresh process every hour anyway; it only needs
# `systemctl daemon-reload` if you changed a .service/.timer file.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="meme"

cd "$APP_DIR"
git pull
chown -R "$APP_USER":"$APP_USER" "$APP_DIR"
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/pip" install -r requirements.txt
sudo -u "$APP_USER" "$APP_DIR/.venv/bin/python" -c "import db; db.init_db()"
systemctl restart meme-dashboard
echo "Deployed. If you edited a .service/.timer file, also run:"
echo "  systemctl daemon-reload && systemctl restart meme-scrape.timer"
