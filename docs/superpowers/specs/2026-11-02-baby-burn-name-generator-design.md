# Baby Burn Playa Name Generator — Design

Date: 2026-11-02 · Status: draft for owner review · **Event: Saturday 2026-11-14**

## Intent

Baby Burn is a one-night house party (~200 guests, until 4am, NY) now in its third year. It began as a way to celebrate new mothers who couldn't go to Burning Man; friends build miniature versions of playa experiences. This project is a kiosk activation: a guest walks up to an iPad, snaps a photo, taps through a few absurd Burning Man / Baby Burn trivia questions, sees an AI "dusty baby" version of themselves, and walks away wearing a printed name badge with their new playa name.

The point is laughs. The quiz is a comedy bit, not a test. The name is a souvenir. The whole thing should take under 30 seconds.

## Goals

- Under 30 seconds from first tap to badge printing; every input is a tap (multiple choice, pick-what's-missing, A/B/C). No typing for guests.
- 300+ hand-approved playa names, each given out at most once, ever (survives reboots).
- Quiz questions mixing real playa lore (warm-Tecate margarita, dust that never washes out) with Baby Burn inside jokes (listed privately in the content repo's `SEEDS.md`).
- Owner reviews every name and every question before go-live; nothing un-approved ever reaches a guest.
- Owner can add questions and names individually, including on the night.
- AI "dusty baby" photo transform shown on screen; guests can grab it on their phone via QR, and opt in to a shared Baby Burn album.

## Non-goals

- Printing the photo on the badge (a small QR link to it is fine).
- Guest accounts, typing names, email/sharing.
- Generating names with AI at runtime (names come only from the approved list).
- Multiple kiosks.

## Anti-goals (failure even if it "works")

- A badge that mentions drugs or politics, or gets deeply personal.
- A repeated name.
- A guest waiting on a spinner, or a Wi-Fi blip stopping badges from printing.
- Real guest names or the private planning sheet ending up in the public repo.
- A guest's photo still online more than 14 days later without their opt-in to the album, or a guest not being able to tell when their photo will be deleted.
- A guest stuck on any screen: every step is skippable.
- Admin or kiosk pages reachable from the public internet.

## Constraints

- **Hardware (owner's):** Raspberry Pi 3, Brother **QL-800** over USB, Brother **DK-1234** die-cut name badge labels (60 × 86 mm, 260 per roll, black only), one iPad as a fixed kiosk.
- **Network:** the house has reliable internet. Pi and iPad join house Wi-Fi. Pi hotspot mode is a documented fallback only (Pi 3's single 2.4 GHz radio can't reliably be client and hotspot at once).
- **Repo:** public GitHub repo `baby-burn-name-generator` under the owner's account. API keys live in a gitignored `.env` on the Pi.
- **Content privacy (owner decision):** the real names, questions and taglines are private. The public repo ships only a tame `content.sample/`. Real content lives in a gitignored `content/` folder. *Builder's call:* `content/` is a clone of a separate **private** GitHub repo (`baby-burn-content`), so it is versioned and backed up. The app reads `content/` if it exists, otherwise `content.sample/`. Content added in `/admin` on the night is written to `content/` too, never to the public tree.
- **Timeline:** event is Sat 2026-11-14. Owner works evenings, with focus days Tue 11-03, Thu 11-05, maybe Fri 11-06, and weekends. Hardware first (Tue 11-03), bake-off Wed 11-04, content review Thu–Fri 11-05/06, drills + soak the 11-07/08 weekend, dress rehearsal Thu 11-12, freeze Fri 11-13.
- **Content rules:** no drugs, no politics, nothing deeply personal. Sex and body jokes allowed but each is flagged `needs_owner: true` and requires explicit owner approval. "Petite/miniature" twists on classic playa names are encouraged.

## Decided by the owner

- Fixed kiosk iPad; Pi hosts the app; QL-800 + DK-1234 labels.
- Names: fixed list, never repeat; survey answers may lightly influence the pick.
- Photo is used to seed the name and for a fun "dusty baby" AI transform; not printed. Fun photos may be saved.
- Cloud AI for the transform is approved. Owner has OpenAI; Google Nano Banana also acceptable. **A trial bake-off between the two decides which one ships.**
- Badge: name prominent, small tagline, first Baby Burn year attended, a fun border.
- Real content private; tame sample public.
- Review via a status field on each name/question.
- Public repo named "baby burn name generator".

## How it works

### Guest flow
0. **Start screen** shows one plain line (*builder's call*): "Your photo goes to an AI to turn you into a dusty baby." Tapping Start means OK.
1. **Snap** (~3s). Live camera preview on the iPad, one big shutter button. Photo uploads to the Pi immediately; the Pi starts the cloud transform and a costume-tagging call in the background.
**Skip anything (owner decision):** every screen has a Skip control: photo, each question, the whole quiz ("Just give me a name"), and the photo/QR screen. Shortest path: Start → Skip photo → Just give me a name → badge prints. A skipped `first_burn` question leaves that line off the badge. Skipped questions contribute no tags.

2. **Quiz** (~20s). One fixed question ("Which Baby Burn was your first?" → 1/2/3, feeds the badge) plus 4 random approved questions. After each tap, a ~1s flash of the "correct" answer and a quip.
3. **Reveal.** Dusty-baby portrait + playa name on screen; badge prints.
4. **Take it home** (only if a photo was taken). A big QR code: "Scan to get your dusty baby photo (deletes in 14 days)". Buttons: "Share to the Baby Burn album (keeps it)", "Delete my photo", "Done". Then the screen resets for the next guest (also auto-resets after ~20s idle).

### Name selection
- Each name has `tags` (e.g. `gnome`, `dust`, `fur`, `sparkle`, `thunderdome`, `sauna`, `latex`, `hole-foods`). Each quiz option may carry tags; the costume tagger returns tags from the same vocabulary.
- Pick (owner decision): collect all unused approved names that share at least one tag with the guest's quiz answers or costume tags, and pick one at random. If none are left (no matching tags, or those categories are used up), pick any random unused approved name.
- "Used" is recorded in SQLite with a uniqueness constraint in the same transaction as the pick, so a double tap or reboot can't repeat a name.
- If unused names run below a threshold, the admin page shows a warning; the owner can add names live.

### Badge
- Rendered on the Pi as a 1-bit image sized for DK-1234 (60 × 86 mm), sent to the QL-800 via the `brother_ql` Python library over USB.
- Tagline (*builder's call*): drawn at random from a pool of ~30 owner-approved one-liners in `taglines.yaml` (same status/review rules as names), e.g. "Dusty since Baby Burn 1".
- Layout: playa name large (auto-fit font size), tagline small, "First Burn: Baby Burn N", decorative black border (one or a few rotating designs).
- One-time printer setup: turn off Editor Lite (hold the button until the green LED goes off) so the printer shows up as a printer, not a USB drive.

### Photo transform
- A single `TRANSFORM_PROVIDER` setting (`openai` | `gemini`) selects the image-edit service; same "dusty baby burner" prompt for both.
- The costume tagger uses the same provider's vision model, so only one API key is needed at the event.
- Timeout (~25s) and any error fall back to the original photo with a local dust filter. **Printing never waits on the cloud.**
- **Phone download (owner decision): photos are hosted in the cloud, not on the Pi.** Each photo session gets an unguessable token at snap time. The Pi uploads the original (right away) and the dusty version (when it lands) to an object-storage bucket, plus a tiny static mobile page at `<PHOTO_BASE_URL>/p/<token>`. The page shows both images with Download buttons and keeps retrying the dusty image ("still developing…") until it appears. The QR codes point to that page. Guests' phones never touch the Pi, so the Pi has no internet-facing server.
- *Builder's call:* the badge also carries a small QR (bottom corner) with the same link, so guests who walk off can still grab it. It's printed only when a photo was taken.
- *Builder's call:* the bucket is reached through the S3-compatible API (`boto3`), so Cloudflare R2, AWS S3, Backblaze B2 and Google Cloud Storage all work by changing settings. **Owner chose Cloudflare R2** (new account; setup automated with a wrangler script plus one manual token step). The bucket allows public reads of single objects only (no listing), and the Pi's key can only write and delete in that one bucket.
- **Retention (owner decision): 14 days**, then the photo is deleted, unless the guest opted in to the album.
- **Album (owner decision):** "Share to the Baby Burn album" is opt-in. It makes the photo visible in a shared album and removes the 14-day timer. The album is a static page at an unguessable `<PHOTO_BASE_URL>/album/<ALBUM_SECRET>/` showing each shared dusty photo with its playa name (tap for the original). It's linked from every guest photo page, and the owner can share it in the group chat. "Delete my photo" also removes it from the album.
- *Builder's call (how expiry works without the Pi running):* images live under one of two prefixes. `tmp/` has a bucket lifecycle rule "delete after 14 days". `album/` has no rule. Sharing copies the images server-side from `tmp/` to `album/` and deletes the `tmp/` copies. The small HTML photo page sits under `p/` with no expiry and holds no images itself, so a QR printed on a badge always opens something: the photo, or "this photo self-destructed". This works the same on R2, S3, B2 and GCS (prefix rules only).
- **Visible deletion timer (owner decision):** the photo page header always shows the status in large type. Either "This photo self-destructs in 13 days, 4 hours (Sat Nov 28)" (a live countdown from the upload time embedded in the page; lifecycle deletion may lag up to a day, so it reads "on or after"), or "Shared to the Baby Burn album. No expiry." After expiry or deletion the page says so instead of showing broken images.
- On the Pi, photos only last for the session (the on-screen reveal), except album photos, which are also kept in `data/album/` as a backup the owner copies off after the event. If an upload fails, the badge still prints and the reveal still works; only the phone link is affected.

### Content & review
- `names.yaml`, `questions.yaml`, `taglines.yaml` in `content/` (private) or `content.sample/` (public). Every entry has `status: draft | approved | rejected`, optional `needs_owner: true`, and `tags`.
- The kiosk only ever loads `approved` entries.
- Owner review before go-live = reading/editing the YAML (or the admin page's review list) and flipping statuses.
- Admin page (`/admin`, PIN-protected) on the Pi: add a question or name (added as `approved` since the owner wrote it), approve/reject drafts, see remaining-name count, test-print a badge, switch transform provider.
- Question types supported: multiple choice (one correct), "what's missing", pick-all-that-apply. All tap-only.

### Trial bake-off (before the build is final)
- A script runs a folder of test photos (owner-supplied, consenting friends) through both providers with the shared prompt and writes a local side-by-side HTML page: images, latency, approximate cost per image. Owner picks the winner and tunes the prompt.

### Ops
- *Builder's call:* if the Pi can sit near the house router it uses Ethernet, and only the iPad uses Wi-Fi. The iPad page retries failed requests automatically.
- Pi runs the app as a systemd service that starts on boot; reachable at `https://babyburn.local`.
- iPad camera access requires HTTPS; a locally generated certificate (mkcert) is installed and trusted on the single kiosk iPad once.
- iPad locked to the page with Guided Access.
- A go-live checklist: printer Editor Lite off, spare label roll, test print, provider key set, `approved` counts checked, bucket lifecycle rule on `tmp/` set to 14 days, album page empty.

## Content seeds

The owner's seed questions and Baby Burn inside jokes are private: `SEEDS.md` in the private `baby-burn-content` repo. Public playa lore: `docs/research/lore.md`.

## Left to the builder

- Web framework and front-end approach (keep it light enough for a Pi 3; no front-end build step needed).
- Exact prompt wording for the transform (tuned during the bake-off).
- Badge border artwork and fonts (must render crisply in 1-bit at 300 dpi).
- Tag vocabulary details.
- Exact admin page layout.
