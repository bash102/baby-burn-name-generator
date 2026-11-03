# Ops: Pi setup, Cloudflare, party night

## 1. Cloudflare from zero

R2 holds the "dusty baby" photos so guests can scan a QR and download them. Free tier: 10 GB storage and free egress, which is enough for a party this size. The public `https://pub-....r2.dev` URL is rate-limited and Cloudflare calls it non-production; that is fine for one party (a custom domain would be the upgrade).

1. Go to <https://dash.cloudflare.com/sign-up>. Enter your email and a password, click **Sign up**.
2. Open the verification email from Cloudflare and click the link. Log back in if asked.
3. In the left sidebar choose **Storage & databases › R2 object storage** (labelled "R2" in older dashboards).
4. Click the button that adds R2 (labelled **Purchase R2 Plan** or **Add R2 subscription**), add a payment method (a card is required even for the free tier), and confirm. You are not charged under the free limits.
5. On your Mac, from the repo root, run `bash deploy/setup-r2.sh`. It needs Node (`npx`) and `uv` (`brew install node uv`).
   - A browser opens for `wrangler login`; click **Allow**.
   - It creates the `baby-burn-photos` bucket, enables the public r2.dev URL (no directory listing), adds the single lifecycle rule (delete `tmp/` after the retention period; nothing on `p/` or `album/`), and prints the URL and your account ID.
6. Create the API token (the one thing wrangler cannot do): dashboard › **R2 object storage** › **API** › **Manage API tokens** › **Create API token**.
   - Permissions: **Object Read & Write**.
   - Specify bucket: only `baby-burn-photos`.
   - Click **Create API Token** and copy the **Access Key ID** and **Secret Access Key** (shown once).
7. Paste both into the script prompts. It writes `PHOTO_BUCKET`, `PHOTO_BASE_URL`, `S3_ENDPOINT_URL`, `S3_REGION=auto`, the keys and a generated `ALBUM_SECRET` into `.env`, then runs `tools/cloud_check.py`.
8. Keep this `.env`; step 4 of section 2 copies its R2 lines to the Pi.

Re-running `setup-r2.sh` is safe; it keeps an existing `ALBUM_SECRET`.

## 2. Pi provisioning

Do this after section 1: you copy a few lines of the Mac `.env` onto the Pi.

1. Flash with Raspberry Pi Imager: choose **Raspberry Pi OS Lite (64-bit)** (bookworm or newer). In Imager's OS customisation set hostname `babyburn`, username `pi` (the service and paths assume `/home/pi`), enable SSH, and optionally Wi-Fi.
2. Until setup finishes, reach the Pi at `babyburn.local` (set in Imager) or its IP address.
3. On the Pi, clone the public repo (no login needed) and run the setup script:
   ```
   git clone https://github.com/bash102/baby-burn-name-generator.git ~/baby-burn-name-generator
   cd ~/baby-burn-name-generator && bash deploy/setup-pi.sh
   ```
   The script:
   - installs packages, sets hostname `babyburn` and timezone America/New_York
   - installs `uv` and runs `uv sync --python 3.11` (the app runs from the checkout, never pip-installed)
   - adds the QL-800 udev rule (`lsusb` should show `04f9:209b`)
   - generates certs with `mkcert` into `certs/` (plus `certs/rootCA.pem` for the iPad)
   - enables `babyburn.service` (`.venv/bin/python -m app.web` on :443, `Restart=always`, no network needed to start)
   - creates `.env` (mode 600) with a random 8-digit `ADMIN_PIN`, printed once; note it
   - does not start the service. Re-runs restart it automatically, unless `content/` is missing.
4. Add the R2 and provider settings. On your Mac, from the project folder, copy the `.env` you made in section 1 (plus your provider keys) to a side file; do not overwrite the Pi's `.env`, which holds the generated `ADMIN_PIN`:
   ```
   scp .env pi@babyburn.local:~/r2.env
   ```
   On the Pi, merge only the R2 and provider lines into the Pi `.env`. Safe to re-run; `ADMIN_PIN` is never touched:
   ```
   cd ~/baby-burn-name-generator
   chmod 600 ~/r2.env
   grep -E '^(PHOTO_BUCKET|PHOTO_BASE_URL|S3_ENDPOINT_URL|S3_REGION|S3_ACCESS_KEY_ID|S3_SECRET_ACCESS_KEY|ALBUM_SECRET|OPENAI_API_KEY|GEMINI_API_KEY|TRANSFORM_PROVIDER)=' ~/r2.env > ~/r2.lines && [ -s ~/r2.lines ] && sed -i -E '/^(PHOTO_BUCKET|PHOTO_BASE_URL|S3_ENDPOINT_URL|S3_REGION|S3_ACCESS_KEY_ID|S3_SECRET_ACCESS_KEY|ALBUM_SECRET|OPENAI_API_KEY|GEMINI_API_KEY|TRANSFORM_PROVIDER)=/d' .env && cat ~/r2.lines >> .env; rm -f ~/r2.env ~/r2.lines
   ```
   Then check the Pi `.env` (`nano .env`):
   - `TRANSFORM_PROVIDER` is `openai` or `gemini`, with the matching `OPENAI_API_KEY` or `GEMINI_API_KEY`
   - `PRINTER=usb://0x04f9:0x209b`
   - leave `ADMIN_PIN` as generated

   The kiosk refuses to start with a real `PHOTO_BUCKET` and the placeholder `PHOTO_BASE_URL` (`example.com`) or `ALBUM_SECRET` (`change-me` or under 16 characters).
5. Copy the private content to the Pi (the Pi needs no GitHub access). Content lives in the PRIVATE repo `bash102/baby-burn-content` (branch `drafts-v1` until reviewed). On your Mac, inside the project folder (`content/` is gitignored):
   ```
   gh repo clone bash102/baby-burn-content content
   git -C content checkout drafts-v1
   rsync -a --delete --exclude .git content/ pi@babyburn.local:~/baby-burn-name-generator/content/
   ```
6. Validate on the Pi: `cd ~/baby-burn-name-generator && uv run --env-file .env python -m app.content content/`. Without `content/` the kiosk would serve the public sample content, so `setup-pi.sh` will not start the service until it exists.
7. Start the service: `sudo systemctl start babyburn` (use `restart` after later edits).

After editing content by hand (on the Pi, or re-syncing from your Mac), run the validate command again before restarting; a broken file stops the kiosk from booting.

## 3. Networking

- Ethernet first: plug the Pi into the venue router. The iPad joins the same network and opens `https://babyburn.local`.
- Hotspot fallback if there is no router: `sudo nmcli dev wifi hotspot ifname wlan0 ssid BABYBURN password <pick-one>`. The Pi then has no internet, so no cloud transform or QR download; names and badges still work.

## 4. iPad

1. AirDrop/email `certs/rootCA.pem` to the iPad and open it (Settings › Profile Downloaded › Install).
2. Settings › General › About › Certificate Trust Settings, then enable full trust for the mkcert root.
3. Safari: open `https://babyburn.local`, Share › Add to Home Screen, launch from the icon.
4. On first use tap **Allow** for the camera.
5. Lock it with Guided Access (Settings › Accessibility › Guided Access; triple-click the side button in the app). Re-check the camera after starting Guided Access.

## 5. Printer

Brother QL-800: turn **Editor Lite off** (the green light on the unit must be off, hold the button until it goes out). Load Brother DK-1234 (DK-11234 outside the US): 60 × 86 mm die-cut name badges; keep a spare roll.

## 6. Go-live checklist

- [ ] `uv run --env-file .env python -m app.content content/` shows the approved counts you expect (names well above the low-names warning).
- [ ] `TRANSFORM_PROVIDER` is not `fake` and `OPENAI_API_KEY` / `GEMINI_API_KEY` is set in `.env`.
- [ ] Test print: `uv run --env-file .env python tools/test_print.py "Lil' Gnomesayer"`; badge is legible and aligned.
- [ ] Spare label roll on hand; Editor Lite off.
- [ ] Bucket lifecycle rule on `tmp/` matches `RETENTION_DAYS` in `app/photos.py` (`npx wrangler r2 bucket lifecycle list baby-burn-photos`).
- [ ] `timedatectl` shows `System clock synchronized: yes` (badge and photo-page dates depend on it).
- [ ] Cloud check, run on the Pi: `uv run --env-file .env python tools/cloud_check.py`. It puts, copies and batch-deletes a test page with the kiosk's own client, prints a URL, and waits for Enter. Open the URL on a phone on cellular (page loads), then open the bucket root URL (no listing), then press Enter.
- [ ] iPad cert trusted, Home Screen icon works, Guided Access on.
- [ ] Freeze (section 8): after the last rehearsal, reset names and album so the party starts clean.

## 7. Party night

Admin: <https://babyburn.local/admin> (any username, the `ADMIN_PIN` from the Pi `.env` as password).

The dashboard shows:
- names left (a warning shows when low)
- the album (shared photos)
- cloud errors (uploads or deletes that failed)

Common jobs:
- Reprint a badge: open the guest's entry in admin and press **Reprint**.
- Switch AI provider: use the provider control in admin (no restart needed).

If something breaks:

| Problem | What to do | What the guest sees |
|---|---|---|
| Badge jam or out of labels | Open the lid, clear or reseat the roll, press **Reprint** | "Badge jammed! Grab a volunteer." |
| Printer unplugged | Plug it back in, then press **Reprint** in `/admin` | "Badge jammed! Grab a volunteer." and they keep their name |
| Kiosk frozen | Triple-click the side/top button to exit Guided Access, close and relaunch the Home Screen app, re-enable Guided Access. If still frozen, on the Pi: `sudo systemctl restart babyburn` | Back to the start screen |
| No internet | Nothing; badges still print | Photos fall back to the dust filter; the phone QR link fails |

Logs: `journalctl -u babyburn -f` on the Pi.

## 8. Freeze (Fri 11-13)

After the last rehearsal, reset the event so the party starts clean: every name is available again, the shared album is empty and no rehearsal photos are left on the Pi. Run this ON THE PI, not your Mac: the album index (`data/album.sqlite3`) and the name database live there.

```
sudo systemctl stop babyburn
cd ~/baby-burn-name-generator && uv run --env-file .env python tools/reset_event.py   # type yes
sudo systemctl start babyburn
```

`reset_event.py` deletes `data/names.sqlite3`, empties the album (same as `tools/album_reset.py`: deletes the `album/<ALBUM_SECRET>/` objects and marks those photo pages deleted) and clears `data/photos/`. Unshared rehearsal photos in `tmp/` expire on their own.

## 9. After the party

- Copy `data/album/` off the Pi (backup of shared photos).
- Unshared photos vanish after `RETENTION_DAYS` in `app/photos.py` (and the matching R2 rule). Album photos stay until you delete the `album/` prefix in the R2 dashboard.

## 10. Review content on your Mac

Run the kiosk locally against your private content repo (`bash102/baby-burn-content`, cloned into `content/`), so edits land there and not in the public sample content:

```
CONTENT_DIR=<path to private content clone> ADMIN_PIN=<pin> uv run --env-file .env python -m app.web --dev
```

`ADMIN_PIN` is any PIN you choose (not empty or `change-me`; those disable the admin pages). `--dev` serves plain HTTP on http://127.0.0.1:8000. Open http://127.0.0.1:8000/admin/review and sign in with any username and the PIN as the password.

On the Pi, use a strong `ADMIN_PIN`: anyone on the party Wi-Fi can reach `/admin`.

Approve and Reject edit the YAML files in your clone. Commit them there when you finish.
