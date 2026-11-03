# Ops: Pi setup, Cloudflare, party night

## 1. Cloudflare from zero

R2 holds the "dusty baby" photos so guests can scan a QR and download them. Free tier: 10 GB storage and free egress, which is enough for a party this size. The public `https://pub-....r2.dev` URL is rate-limited and Cloudflare calls it non-production; that is fine for one party (a custom domain would be the upgrade).

1. Go to <https://dash.cloudflare.com/sign-up>. Enter your email and a password, click **Sign up**.
2. Open the verification email from Cloudflare and click the link. Log back in if asked.
3. In the left sidebar choose **Storage & databases › R2 object storage** (labelled "R2" in older dashboards).
4. Click the button that adds R2 (labelled **Purchase R2 Plan** or **Add R2 subscription**), add a payment method (a card is required even for the free tier), and confirm. You are not charged under the free limits.
5. On your Mac, from the repo root, run `bash deploy/setup-r2.sh`. It needs Node (`npx`) and `uv` (`brew install node uv`).
   - A browser opens for `wrangler login`; click **Allow**.
   - It creates the `baby-burn-photos` bucket, enables the public r2.dev URL (no directory listing), adds the single lifecycle rule (delete `tmp/` after 14 days; nothing on `p/` or `album/`), and prints the URL and your account ID.
6. Create the API token (the one thing wrangler cannot do): dashboard › **R2 object storage** › **API** › **Manage API tokens** › **Create API token**.
   - Permissions: **Object Read & Write**.
   - Specify bucket: only `baby-burn-photos`.
   - Click **Create API Token** and copy the **Access Key ID** and **Secret Access Key** (shown once).
7. Paste both into the script prompts. It writes `PHOTO_BUCKET`, `PHOTO_BASE_URL`, `S3_ENDPOINT_URL`, `S3_REGION=auto`, the keys and a generated `ALBUM_SECRET` into `.env`, then runs `tools/cloud_check.py`.
8. Copy `.env` to the Pi after section 2 (the Pi must be set up first); see the end of section 2.

Re-running `setup-r2.sh` is safe; it keeps an existing `ALBUM_SECRET`.

## 2. Pi provisioning

Flash Raspberry Pi OS (bookworm), clone the repo to `/home/pi/baby-burn-name-generator`, then:

```
cd ~/baby-burn-name-generator && bash deploy/setup-pi.sh
```

The script installs packages, sets hostname `babyburn`, installs `uv` and runs `uv sync --python 3.11` (the app runs from the checkout, never pip-installed), adds the QL-800 udev rule (`lsusb` should show `04f9:209b`), generates certs with `mkcert` into `certs/` (plus `certs/rootCA.pem` for the iPad), and enables `babyburn.service` (`.venv/bin/python -m app.web` on :443, `Restart=always`, no network needed to start).

1. Run `bash deploy/setup-pi.sh` (above). The first run creates `.env` (mode 600) and does not start the service. Re-runs restart the service automatically.
2. Edit `.env`: `TRANSFORM_PROVIDER` (`openai` or `gemini`), the matching `OPENAI_API_KEY` or `GEMINI_API_KEY`, `ADMIN_PIN`, `PRINTER=usb://0x04f9:0x209b`. If you set up R2 in section 1, copy its values over now by copying the whole `.env` from your Mac:
   `scp .env pi@babyburn.local:~/baby-burn-name-generator/.env` then `ssh pi@babyburn.local chmod 600 baby-burn-name-generator/.env`. Then set the items above in it; the Pi needs no other change.
3. Start the service: `sudo systemctl start babyburn` (use `restart` after later edits).

Logs: `journalctl -u babyburn -f`.

## 3. Networking

- Ethernet first: plug the Pi into the venue router. The iPad joins the same network and opens `https://babyburn.local`.
- Hotspot fallback if there is no router: `sudo nmcli dev wifi hotspot ifname wlan0 ssid BABYBURN password <pick-one>`. The Pi then has no internet, so no cloud transform or QR download; names and badges still work.

## 4. iPad

1. AirDrop/email `certs/rootCA.pem` to the iPad and open it (Settings › Profile Downloaded › Install).
2. Settings › General › About › Certificate Trust Settings, then enable full trust for the mkcert root.
3. Safari: open `https://babyburn.local`, Share › Add to Home Screen, launch from the icon.
4. Lock it with Guided Access (Settings › Accessibility › Guided Access; triple-click the side button in the app).

## 5. Printer

Brother QL-800: turn **Editor Lite off** (the green light on the unit must be off, hold the button until it goes out). Load DK-1234 (60x86 mm) labels; keep a spare roll.

## 6. Go-live checklist

- [ ] `uv run --env-file .env python -m app.content content/` shows the approved counts you expect (names well above the low-names warning (25 left)).
- [ ] `TRANSFORM_PROVIDER` is not `fake` and `OPENAI_API_KEY` / `GEMINI_API_KEY` is set in `.env`.
- [ ] Test print from the app; badge is legible and aligned.
- [ ] Spare label roll on hand; Editor Lite off.
- [ ] Bucket lifecycle rule on `tmp/` is 14 days (`npx wrangler r2 bucket lifecycle list baby-burn-photos`).
- [ ] Album page is empty. To clear the album after testing, run `uv run --env-file .env python tools/album_reset.py` (arrives with the kiosk server update) or delete the `album/` prefix in the R2 dashboard.
- [ ] Cloud check from a phone on cellular: `uv run --env-file .env python tools/cloud_check.py`, open the printed URL on the phone (page loads), then open the bucket root URL (no listing).
- [ ] iPad cert trusted, Home Screen icon works, Guided Access on.

## 7. After the party

- Copy `data/album/` off the Pi (backup of shared photos).
- Unshared photos vanish 14 days after upload (lifecycle on `tmp/`). Album photos stay until you delete the `album/` prefix in the R2 dashboard.

## 8. Review content on your Mac

The admin pages need `ADMIN_PIN` (empty or `change-me` disables them) and refuse edits while the public sample content is loaded. Point at your private content clone:

```
CONTENT_DIR=<path to private content clone> ADMIN_PIN=<pin> uv run --env-file .env python -m app.web --dev
```

Then open http://127.0.0.1:8000/admin/review (any username, the PIN as password). Approve/Reject write straight into the clone YAML files; commit them there when you are done.
