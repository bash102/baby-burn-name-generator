#!/usr/bin/env bash
# Set up the Cloudflare R2 photo bucket from the owner's Mac. Idempotent.
# Needs Node (npx) and a Cloudflare account (docs/ops.md, "Cloudflare from zero").
# Run from the repo root:  bash deploy/setup-r2.sh
# Wrangler commands verified against https://developers.cloudflare.com/workers/wrangler/commands/r2/
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
BUCKET=baby-burn-photos
RULE=expire-tmp
# Single source: RETENTION_DAYS in app/photos.py (fallback 14 if uv is missing).
RETENTION_DAYS="$(uv run --python 3.11 python -c 'from app.photos import RETENTION_DAYS; print(RETENTION_DAYS)' 2>/dev/null || echo 14)"
W="npx --yes wrangler"

echo "== 1/5 wrangler login (opens your browser; skipped if already logged in)"
$W whoami >/dev/null 2>&1 || $W login

echo "== 2/5 bucket"
if $W r2 bucket list | grep -qw "$BUCKET"; then
  echo "bucket $BUCKET exists"
else
  $W r2 bucket create "$BUCKET"
fi

echo "== 3/5 public r2.dev URL (no listing)"
$W r2 bucket dev-url enable "$BUCKET" -y
DEV_URL="$($W r2 bucket dev-url get "$BUCKET" | grep -Eo 'https://pub-[A-Za-z0-9.-]+\.r2\.dev' | head -n1 || true)"
[ -n "$DEV_URL" ] || read -r -p "Could not detect the public URL. Paste it (https://pub-....r2.dev): " DEV_URL
DEV_URL="${DEV_URL%/}"

echo "== 4/5 lifecycle: delete tmp/ after $RETENTION_DAYS days (only rule; nothing on p/ or album/)"
if $W r2 bucket lifecycle list "$BUCKET" | grep -q "$RULE"; then
  echo "rule $RULE exists"
else
  $W r2 bucket lifecycle add "$BUCKET" "$RULE" tmp/ --expire-days "$RETENTION_DAYS" -y
fi

ACCOUNT_ID="$($W whoami --json 2>/dev/null | python3 -c 'import json,sys; a=json.load(sys.stdin).get("accounts") or []; print(a[0]["id"] if len(a)==1 else "")' 2>/dev/null || true)"
[ -n "$ACCOUNT_ID" ] || read -r -p "Account ID (dashboard -> R2 overview, right side): " ACCOUNT_ID

cat <<EOF

Public URL: $DEV_URL
Account ID: $ACCOUNT_ID

== 5/5 One manual step: create the API token (wrangler cannot do this)
  1. dash.cloudflare.com -> R2 object storage -> "API" / "Manage API tokens"
  2. Create Account API token (or "Create API token")
  3. Permission: "Object Read & Write"
  4. Specify bucket(s): only $BUCKET
  5. Create, then copy the Access Key ID and Secret Access Key (shown once).
EOF
read -r -p "Access Key ID: " KEY_ID
read -r -s -p "Secret Access Key (hidden): " SECRET; echo
[ -n "$KEY_ID" ] && [ -n "$SECRET" ] || { echo "Both key values are required." >&2; exit 1; }

[ -f .env ] || cp .env.example .env
chmod 600 .env  # holds secrets
# Keep an existing ALBUM_SECRET so already-shared album links keep working on re-run.
ALBUM_SECRET="$(sed -n 's/^ALBUM_SECRET=//p' .env | head -n1)"
if [ -z "$ALBUM_SECRET" ] || [ "$ALBUM_SECRET" = change-me ]; then
  ALBUM_SECRET="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"
fi

set_env() { # set_env KEY VALUE: replace or append in .env
  local tmp; tmp="$(mktemp)"
  grep -v "^$1=" .env >"$tmp" || true
  printf '%s=%s\n' "$1" "$2" >>"$tmp"
  cat "$tmp" >.env; rm -f "$tmp"
}
set_env PHOTO_BUCKET "$BUCKET"
set_env PHOTO_BASE_URL "$DEV_URL"
set_env S3_ENDPOINT_URL "https://$ACCOUNT_ID.r2.cloudflarestorage.com"
set_env S3_REGION auto
set_env S3_ACCESS_KEY_ID "$KEY_ID"
set_env S3_SECRET_ACCESS_KEY "$SECRET"
set_env ALBUM_SECRET "$ALBUM_SECRET"
echo "Wrote R2 settings to .env"

echo "== cloud check"
uv run --python 3.11 --env-file .env python tools/cloud_check.py
