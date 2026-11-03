#!/usr/bin/env bash
# Provision the Raspberry Pi. Run as the `pi` user from the checkout:
#   cd ~/baby-burn-name-generator && bash deploy/setup-pi.sh
# Idempotent: safe to re-run.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST=babyburn

[ "$(id -u)" -ne 0 ] || { echo "Run as the pi user (not root); sudo is used where needed." >&2; exit 1; }
[ "$REPO" = /home/pi/baby-burn-name-generator ] || echo "WARN: babyburn.service assumes /home/pi/baby-burn-name-generator, repo is $REPO" >&2

echo "== apt packages"
sudo apt-get update
# mkcert is in Debian bookworm+ (Raspberry Pi OS); libnss3-tools lets it manage the local CA
sudo apt-get install -y python3-venv libusb-1.0-0 avahi-daemon git curl mkcert libnss3-tools usbutils

echo "== hostname"
[ "$(hostname)" = "$HOST" ] || sudo hostnamectl set-hostname "$HOST"
grep -q "127.0.1.1[[:space:]]*$HOST" /etc/hosts || echo "127.0.1.1 $HOST" | sudo tee -a /etc/hosts >/dev/null
sudo systemctl enable --now avahi-daemon

echo "== uv + python deps (run from the checkout, never pip-installed)"
command -v uv >/dev/null || [ -x "$HOME/.local/bin/uv" ] || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
(cd "$REPO" && uv sync --python 3.11)

echo "== udev rule for Brother QL-800 (04f9:209b)"
sudo install -m 644 "$REPO/deploy/99-brother-ql.rules" /etc/udev/rules.d/99-brother-ql.rules
sudo udevadm control --reload-rules
sudo udevadm trigger
lsusb | grep -q '04f9:209b' || echo "NOTE: QL-800 (04f9:209b) not seen in lsusb; plug it in and power it on (Editor Lite light off)."

echo "== TLS certs (mkcert)"
mkdir -p "$REPO/certs"
mkcert -install
if [ ! -f "$REPO/certs/$HOST.local.pem" ]; then
  mkcert -cert-file "$REPO/certs/$HOST.local.pem" -key-file "$REPO/certs/$HOST.local-key.pem" "$HOST.local"
fi
cp "$(mkcert -CAROOT)/rootCA.pem" "$REPO/certs/rootCA.pem"  # install this on the iPad

echo "== .env"
ENV_NEW=0
if [ ! -f "$REPO/.env" ]; then cp "$REPO/.env.example" "$REPO/.env"; ENV_NEW=1; fi
chmod 600 "$REPO/.env"  # holds API keys and ADMIN_PIN

echo "== systemd unit"
sudo install -m 644 "$REPO/deploy/babyburn.service" /etc/systemd/system/babyburn.service
sudo systemctl daemon-reload
sudo systemctl enable babyburn.service
if [ "$ENV_NEW" -eq 1 ]; then
  echo "NOTE: created .env from .env.example; service enabled but NOT started."
  echo "Edit $REPO/.env (provider keys, ADMIN_PIN, PRINTER=usb://0x04f9:0x209b), then: sudo systemctl start babyburn"
else
  sudo systemctl restart babyburn.service
fi

echo "Done. iPad root CA: $REPO/certs/rootCA.pem   URL: https://$HOST.local"
