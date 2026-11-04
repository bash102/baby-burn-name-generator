#!/usr/bin/env bash
# Provision a Raspberry Pi. Run as a normal (non-root) user from the checkout:
#   bash deploy/setup-pi.sh
# Idempotent: safe to re-run. Settings (env, all optional):
#   APP_USER      service user (default: current user)
#   KIOSK_HOST    mDNS name, certs are for $KIOSK_HOST.local (default: babyburn)
#   KIOSK_PORT    TLS port (default: 443)
#   SET_HOSTNAME  yes|no: set the Pi hostname to KIOSK_HOST (default: yes)
#   SET_TIMEZONE  yes|no: set America/New_York (default: yes)
#   DRY_RUN=1     print every system change instead of making it (unset, 0 or 1 only)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_USER="${APP_USER:-$(id -un)}"
HOST="${KIOSK_HOST:-babyburn}"
PORT="${KIOSK_PORT:-443}"
SET_HOSTNAME="${SET_HOSTNAME:-yes}"
SET_TIMEZONE="${SET_TIMEZONE:-yes}"
DRY_RUN="${DRY_RUN-0}"  # empty is an error below, not 0

[ "$(id -u)" -ne 0 ] || { echo "Run as a normal user (not root); sudo is used where needed." >&2; exit 1; }
case "$DRY_RUN" in 0|1) ;; *) echo "DRY_RUN must be unset, 0 or 1, got '$DRY_RUN'" >&2; exit 1;; esac
[ "$APP_USER" = "$(id -un)" ] || { echo "APP_USER ($APP_USER) must be the user running this script ($(id -un))." >&2; exit 1; }
case "$PORT" in ''|*[!0-9]*|0*|??????*) echo "KIOSK_PORT must be 1-65535, got '$PORT'" >&2; exit 1;; esac
[ "$PORT" -le 65535 ] || { echo "KIOSK_PORT must be 1-65535, got '$PORT'" >&2; exit 1; }
for v in SET_HOSTNAME SET_TIMEZONE; do
  case "${!v}" in yes|no) ;; *) echo "$v must be yes or no, got '${!v}'" >&2; exit 1;; esac
done

# Run a system-changing command, or just print it under DRY_RUN=1.
run() { if [ "$DRY_RUN" = 1 ]; then echo "DRY-RUN: $*"; else "$@"; fi; }
[ "$DRY_RUN" != 1 ] || echo "DRY RUN: nothing below is executed. user=$APP_USER repo=$REPO host=$HOST.local port=$PORT"

echo "== apt packages"
MISSING=()
for p in python3-venv libusb-1.0-0 avahi-daemon git curl mkcert usbutils; do
  dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q 'install ok installed' || MISSING+=("$p")
done
if [ "${#MISSING[@]}" -eq 0 ]; then
  echo "all required packages already installed"
else
  echo "will install: ${MISSING[*]}"
  run sudo apt-get update
  run sudo apt-get install -y "${MISSING[@]}"
fi

echo "== hostname"
if [ "$SET_HOSTNAME" = yes ]; then
  [ "$(hostname)" = "$HOST" ] || run sudo hostnamectl set-hostname "$HOST"
  grep -q "127.0.1.1[[:space:]]*$HOST" /etc/hosts 2>/dev/null || run sudo sh -c "echo '127.0.1.1 $HOST' >> /etc/hosts"
  run sudo systemctl enable --now avahi-daemon
else
  echo "SET_HOSTNAME=no: hostname and /etc/hosts untouched"
fi

echo "== timezone (badge and photo-page dates)"
if [ "$SET_TIMEZONE" = yes ]; then
  run sudo timedatectl set-timezone America/New_York
else
  echo "SET_TIMEZONE=no: timezone untouched"
fi

echo "== uv + python deps (run from the checkout, never pip-installed)"
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN: install uv if missing; (cd $REPO && uv sync --python 3.11)"
else
  UV="$HOME/.local/bin/uv"
  [ -x "$UV" ] || curl -LsSf https://astral.sh/uv/install.sh | UV_NO_MODIFY_PATH=1 sh
  (cd "$REPO" && "$UV" sync --python 3.11)
fi

echo "== udev rule for Brother QL-800 (04f9:209b)"
run sudo install -m 644 "$REPO/deploy/99-brother-ql.rules" /etc/udev/rules.d/99-brother-ql.rules
run sudo udevadm control --reload-rules
run sudo udevadm trigger --subsystem-match=usb --attr-match=idVendor=04f9
if [ "$DRY_RUN" != 1 ]; then
  lsusb | grep -q '04f9:209b' || echo "NOTE: QL-800 (04f9:209b) not seen in lsusb; plug it in and power it on (Editor Lite light off)."
fi

echo "== TLS certs (mkcert) for $HOST.local"
CERT="certs/$HOST.local.pem"
KEY="certs/$HOST.local-key.pem"
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN: mkcert -cert-file $REPO/$CERT -key-file $REPO/$KEY $HOST.local (if missing); copy rootCA.pem to $REPO/certs/"
else
  mkdir -p "$REPO/certs"
  if [ ! -f "$REPO/$CERT" ]; then
    mkcert -cert-file "$REPO/$CERT" -key-file "$REPO/$KEY" "$HOST.local"
  fi
  # mkcert makes its CA on first use. No -install: only the iPad needs to trust it.
  CAROOT_PEM="$(mkcert -CAROOT)/rootCA.pem"
  [ -f "$CAROOT_PEM" ] || mkcert -cert-file "$REPO/$CERT" -key-file "$REPO/$KEY" "$HOST.local"
  cp "$CAROOT_PEM" "$REPO/certs/rootCA.pem"  # install this on the iPad
fi

echo "== .env"
ENV_NEW=0
[ -f "$REPO/.env" ] || ENV_NEW=1
# Append KEY=VALUE to .env unless KEY is already set there (an existing value wins; warn if it differs).
env_default() {
  if [ -f "$REPO/.env" ] && grep -q "^$1=" "$REPO/.env"; then
    old="$(sed -n "s/^$1=//p" "$REPO/.env" | tail -1)"
    echo ".env: $1 already set, kept"
    [ "$old" = "$2" ] || echo "WARNING: .env keeps $1=$old, but KIOSK_HOST/KIOSK_PORT imply $2. Edit $REPO/.env to change it."
  elif [ "$DRY_RUN" = 1 ]; then
    echo "DRY-RUN: .env gets $1=$2"
  else
    [ -z "$(tail -c1 "$REPO/.env")" ] || echo >> "$REPO/.env"
    echo "$1=$2" >> "$REPO/.env"
  fi
}
if [ "$DRY_RUN" = 1 ]; then
  [ "$ENV_NEW" -eq 0 ] || echo "DRY-RUN: create .env from .env.example (mode 600, random ADMIN_PIN)"
else
  if [ "$ENV_NEW" -eq 1 ]; then cp "$REPO/.env.example" "$REPO/.env"; fi
  chmod 600 "$REPO/.env"  # holds API keys and ADMIN_PIN
  if [ "$ENV_NEW" -eq 1 ] && grep -qx 'ADMIN_PIN=change-me' "$REPO/.env"; then
    PIN="$(python3 -c 'import secrets; print(f"{secrets.randbelow(10**8):08d}")')"
    sed -i "s/^ADMIN_PIN=change-me\$/ADMIN_PIN=$PIN/" "$REPO/.env"
    echo "ADMIN PIN (shown once, also in .env): $PIN"
  fi
fi
env_default LISTEN_PORT "$PORT"
env_default TLS_CERT "$CERT"
env_default TLS_KEY "$KEY"

echo "== systemd unit"
# Port from .env wins over KIOSK_PORT on re-runs, so the unit matches what the app listens on.
UNIT_PORT="$PORT"
if [ -f "$REPO/.env" ]; then
  P="$(sed -n 's/^LISTEN_PORT=//p' "$REPO/.env" | tail -1)"
  case "$P" in ''|*[!0-9]*) ;; *) UNIT_PORT="$P";; esac
fi
CAPS="# port >= 1024: no bind capability needed"
[ "$UNIT_PORT" -ge 1024 ] || CAPS="AmbientCapabilities=CAP_NET_BIND_SERVICE"
UNIT="$(sed -e '/^## /d' -e "s|@USER@|$APP_USER|g" -e "s|@REPO@|$REPO|g" -e "s|@PORT_CAPS@|$CAPS|g" "$REPO/deploy/babyburn.service")"
if [ "$DRY_RUN" = 1 ]; then
  echo "DRY-RUN: write /etc/systemd/system/babyburn.service:"
  printf '%s\n' "$UNIT" | sed 's/^/    | /'
else
  printf '%s\n' "$UNIT" | sudo install -m 644 /dev/stdin /etc/systemd/system/babyburn.service
fi
run sudo systemctl daemon-reload
run sudo systemctl enable babyburn.service
if [ ! -d "$REPO/content" ]; then
  echo "NOTE: no private content in $REPO/content; service enabled but NOT started (it would serve sample content)."
  echo "On your Mac: rsync -a --delete <private content clone>/ $APP_USER@$HOST.local:$REPO/content/"
  echo "Then on the Pi: cd $REPO && ~/.local/bin/uv run --env-file .env python -m app.content content/ && sudo systemctl start babyburn"
elif [ "$ENV_NEW" -eq 1 ]; then
  echo "NOTE: created .env from .env.example; service enabled but NOT started."
  echo "Edit $REPO/.env (provider keys, ADMIN_PIN, PRINTER=usb://0x04f9:0x209b), then: sudo systemctl start babyburn"
else
  run sudo systemctl restart babyburn.service
fi

URL="https://$HOST.local"
[ "$UNIT_PORT" = 443 ] || URL="$URL:$UNIT_PORT"
echo "Done. iPad root CA: $REPO/certs/rootCA.pem   URL: $URL"
