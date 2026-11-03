# Baby Burn Playa Name Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Tasks in the same wave are independent: dispatch them in parallel, each in its own git worktree, and merge wave by wave.

**Goal:** A Raspberry Pi kiosk where a guest snaps a photo on an iPad, answers 5 tap-only trivia questions, sees an AI "dusty baby" portrait they can grab on their phone via QR, and gets a never-repeated playa name printed on a Brother QL-800 badge in under 30 seconds.

**Architecture:** One Flask app on the Pi serves a static kiosk page (vanilla JS, no build step), a JSON API, and a PIN-protected admin. Content is YAML (private `content/` or public `content.sample/`). Used names are stored in SQLite. Badges are rendered with Pillow and printed with `brother_ql_next` over USB. The photo transform and costume tagging run in a background thread pool against OpenAI or Gemini, and printing never waits on them.

**Tech Stack:** Cloudflare R2 for guest photos (via wrangler for setup, boto3 at runtime), Python 3.11 (Raspberry Pi OS Lite 64-bit, Bookworm), Flask, PyYAML, Pillow, segno (QR codes, pure Python), boto3 (S3-compatible cloud bucket), brother_ql_next 0.12 (has `60x86` label + `QL-800`), openai, google-genai, sqlite3 (stdlib), pytest. Front end: one HTML file + vanilla JS + CSS.

**Spec:** `docs/superpowers/specs/2026-11-02-baby-burn-name-generator-design.md`. Lore source: `docs/research/lore.md`.

## Global Constraints

- Event: **Saturday 2026-11-14**. Release candidate by Sun 11-08; code freeze Fri 11-13. Owner hands-on time: weekday evenings, focus days Tue 11-03 / Thu 11-05 / Fri 11-06, weekends.
- Guest flow under 30 s; guest input is tap-only (no keyboard).
- A name is given out at most once, ever; enforced by SQLite `PRIMARY KEY`, survives reboot.
- Kiosk serves only `status: approved` entries. Statuses: `draft | approved | rejected`. `needs_owner: true` marks sex/body humor.
- No drugs, no partisan politics (gerrymandering and election-process jokes are in; no real politicians, named parties, or culture-war topics; owner decision 2026-11-03), nothing deeply personal, no real people's names in any content (private included).
- Public repo `baby-burn-name-generator` contains **no** real content, guest names, planning-sheet data, photos, or keys. `content/`, `data/`, `.env`, `certs/` are gitignored.
- App reads `CONTENT_DIR` if it exists, else `content.sample/`. Admin writes only to `CONTENT_DIR`.
- Label: DK-1234 die-cut, brother_ql identifier `60x86`, printable 672 × 954 dots. Badge rendered landscape 954 × 672, mode `"1"`, sent rotated 90°.
- Printer: QL-800 over USB, "Editor Lite" must be OFF.
- Photo transform timeout `TRANSFORM_TIMEOUT_S=25`; failure → kiosk shows original photo with CSS dust filter.
- Every guest step is skippable: photo, each question, whole quiz, photo/QR screen. Skipped `first_burn` → no first-burn line on the badge.
- Cloud layout: page `p/<token>` (HTML, never expires, no images inside); images `tmp/<token>/original.jpg` + `tmp/<token>/dusty.jpg` (bucket lifecycle rule: delete `tmp/` after **14 days**); album images `album/<ALBUM_SECRET>/<token>/…` + album page `album/<ALBUM_SECRET>/index.html` (no expiry). "Share to album" = server-side copy `tmp/`→`album/`, delete `tmp/`, re-put page. "Delete my photo" deletes all images for the token (tmp + album), re-puts the page as deleted, and re-puts the album page. Local copies in `data/photos/<token>/` are deleted at session end; album photos are also kept in `data/album/`. `ALBUM_SECRET` = `secrets.token_urlsafe(16)`, in `.env`.
- The photo page always shows the deletion status prominently: live countdown "self-destructs in N days, H hours (on or after <date>)", or "Shared to the Baby Burn album. No expiry.", or "This photo self-destructed / was deleted."
- QR link = `<PHOTO_BASE_URL>/p/<token>`; the token is `secrets.token_urlsafe(16)`. The Pi exposes nothing to the internet. `/admin` requires `ADMIN_PIN` (HTTP Basic, any username).
- Cloud upload failure never blocks the guest: badge prints, reveal shows; the failure is counted on `/admin`.
- Cloud upload failure never blocks the guest: badge prints, reveal shows; the failure is counted on `/admin`.
- Quiz = fixed `first_burn` question + `QUIZ_RANDOM_COUNT=4` random approved questions.
- Name pick (owner rule): random unused approved name sharing ≥1 tag with the guest's tags; if none, random unused approved; if none, `Dusty Stranger #<n>`.
- Low-names warning at `LOW_NAMES_WARNING=25` remaining.

## Review Focus

1. **Duplicate "finish" request** (double-tap, iPad retry after Wi-Fi blip): must return the same name and print once. Pinned in Task 9 `test_finish_is_idempotent`.
2. **Printer failure** (unplugged, out of labels, Editor Lite on): guest still sees their name, kiosk says "grab a volunteer", admin can reprint. Pinned in Task 9 `test_print_failure_keeps_name` and Task 10 `test_reprint`.
3. **Very long or odd names** (40 chars, apostrophes, accents, emoji): badge text fits; validator rejects characters the font can't draw. Pinned in Task 3 `test_long_name_fits` and Task 1 `test_rejects_unrenderable_chars`.
4. **Guest walks away mid-flow, camera denied, or skips everything:** abandoned sessions expire and delete photos without consuming a name; no-photo and zero-answer sessions still get a name and badge. Pinned in Task 9 `test_abandoned_session_expires`, `test_session_without_photo`, `test_skip_everything`.
5. **Hand-edited YAML is broken while the kiosk is running:** the app keeps the last good content and admin shows the error. Pinned in Task 1 `test_invalid_yaml_raises_contenterror` and Task 10 `test_reload_failure_keeps_old_content`.

---

## Parallelization Map

| Wave | Tasks (parallel within a wave) | Needs | Agents finish by |
|---|---|---|---|
| 0 | T0 repo skeleton, config, schema, sample content | - | Mon 11-02 night |
| 1 | T1 content · T2 names · T3 badge · T4 printer · T5 transform + bake-off · T6 content drafting · T7 Pi ops · T8 kiosk UI | T0 | Tue 11-03 midday |
| 2 | T9 kiosk API + sessions (integration) | T1–T5 | Wed 11-04 |
| 3 | T10 admin · T8 wiring check against live API | T9 | Thu 11-05 morning |
| 4 | T11 hardware bring-up, drills, soak (with owner) | T7, T10 | Sat–Sun 11-07/08 |

**Owner schedule** (owner has weekday evenings, focus days Tue 11-03, Thu 11-05, maybe Fri 11-06, and more time on the weekend). Hands-on hardware and content come first; week 2 is buffer.

| Day | Owner (≈ time) | What it unblocks |
|---|---|---|
| Mon 11-02 eve | Approve plan; put OpenAI + Gemini keys in `.env` (10 min) | Waves 0–1 start |
| **Tue 11-03 focus** | Flash 64-bit Pi OS Lite, SSH on; create a Cloudflare account, add a card (R2 requires one even on the free tier), run `deploy/setup-r2.sh` on the Mac (≈ 20 min total); QL-800 Editor Lite off, plug printer in, run `deploy/setup-pi.sh` and `tools/test_print.py` (2–3 h) | Printer and label orientation proven on day 2; photo links proven from a phone |
| Wed 11-04 eve | Bake-off: 5–10 friends' photos through `tools/bakeoff.py`, pick provider + prompt (45 min) | Provider locked |
| **Thu 11-05 focus** | Content review pass 1 in `/admin/review` on the Mac: names, questions, taglines; flag rewrites; add your own inside jokes (3 h) | Approved pool exists |
| Fri 11-06 (maybe focus) | Finish review; first full guest run on Pi + iPad (cert trust, Guided Access), scan QR on Wi-Fi and cellular (2 h) | End-to-end proven |
| Sat–Sun 11-07/08 | T11 drills + 30-run soak with a couple of friends timing it; content pass 2 on anything the drafts missed (3–4 h) | Release candidate |
| Mon–Wed 11-09/11 eves | Buffer: fixes from the weekend, final content tweaks (≤ 1 h/eve) | - |
| Thu 11-12 eve | Dress rehearsal at the house: Ethernet spot, iPad stand, lighting for photos (1 h) | - |
| Fri 11-13 eve | Freeze. Reset name DB + empty test photos from the album (with confirmation), pack: spare DK-1234 roll, Ethernet cable, power strip, iPad charger (30 min) | - |
| **Sat 11-14** | Baby Burn 3 | - |

---

### Task 0: Repo skeleton, config, content schema, sample content

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `README.md`, `app/__init__.py`, `app/config.py`, `content.sample/names.yaml`, `content.sample/questions.yaml`, `content.sample/taglines.yaml`, `tests/conftest.py`
- Move in: existing `docs/` (spec, research)

**Interfaces:**
- Produces: `app.config.Config` (frozen dataclass) via `Config.from_env(env: Mapping[str,str] = os.environ) -> Config` with fields `content_dir: Path` (resolved: `CONTENT_DIR` if exists else `content.sample`), `data_dir: Path` (default `data/`), `transform_provider: str` (`openai|gemini|fake`, default `fake`), `openai_api_key`, `gemini_api_key`, `openai_image_model`, `openai_vision_model`, `gemini_image_model`, `gemini_vision_model` (str, defaults verified against provider docs on the day), `printer: str` (`usb://0x04f9:0x209b` or `file`, default `file`), `admin_pin: str`, `transform_timeout_s: float = 25`, `quiz_random_count: int = 4`, `low_names_warning: int = 25`, `session_ttl_s: int = 600`, `photo_bucket: str`, `photo_base_url: str`, `album_secret: str`, `photo_retention_days: int = 14` (public URL prefix of the bucket, no trailing slash), `s3_endpoint_url: str | None` (`None` = AWS), `s3_region: str = "auto"`, `s3_access_key_id`, `s3_secret_access_key`.
- Produces: content YAML schema (below) used by T1, T6, T9, T10.
- Produces: `tests/conftest.py` fixture `sample_content_dir(tmp_path) -> Path` (copy of `content.sample/`).

Content schema (exact):

```yaml
# names.yaml: list of
- id: lil-gnomesayer          # slug, unique across file
  name: "Lil' Gnomesayer"
  tags: [gnome]
  status: approved            # draft | approved | rejected
  needs_owner: false
  note: ""                    # free text for the owner (YAML comments are NOT preserved)
# questions.yaml: list of
- id: first_burn              # exactly one with fixed: first_burn
  fixed: first_burn
  type: single
  prompt: "Which Baby Burn was your first?"
  options:
    - {id: bb1, text: "Baby Burn 1 (OG dust)", first_burn: 1}
    - {id: bb2, text: "Baby Burn 2", first_burn: 2}
    - {id: bb3, text: "This one! Ring the bell!", first_burn: 3}
  status: approved
- id: margarita
  type: single                # single | multi
  prompt: "What goes in a Burning Man margarita?"
  options:
    - {id: a, text: "Gatorade, a warm Tecate, a little water", correct: true, tags: [dust]}
    - {id: b, text: "Tequila, lime, salt"}
  quip: "Hydration is a cocktail."
  status: approved
  needs_owner: false
# taglines.yaml: list of
- {id: dusty-since, text: "Dusty since the womb", status: approved}
```

- [ ] **Step 1:** `git init`. Write `.gitignore` with `content/`, `data/`, `.env`, `certs/`, `__pycache__/`, `.venv/`. Write `pyproject.toml` (deps: flask, pyyaml, pillow, segno, boto3, brother_ql_next, openai, google-genai; dev: pytest; `requires-python = ">=3.11"`).
- [ ] **Step 2:** Write tame sample content: 20 names across ≥5 tags, 6 questions (the fixed `first_burn`, ≥1 `multi`), 5 taglines. Nothing private.
- [ ] **Step 3: Failing test** `tests/test_config.py::test_content_dir_falls_back_to_sample`: `Config.from_env({"CONTENT_DIR": "/nope"}).content_dir.name == "content.sample"`; `test_defaults`: provider `fake`, printer `file`, timeout `25`.
- [ ] **Step 4:** Implement `Config.from_env`. Run `pytest -q`. Expected: PASS.
- [ ] **Step 5:** `gh repo create baby-burn-name-generator --public --source . --push` and `gh repo create baby-burn-content --private`; clone the private one into `content/`. Commit `chore: skeleton, config, content schema, sample content`.

---

### Task 1: Content loading, validation, writing (wave 1)

**Files:** Create `app/content.py`, `tests/test_content.py`

**Interfaces:**
- Consumes: schema from T0.
- Produces:
  - dataclasses `Name(id, name, tags: frozenset[str], status, needs_owner, note)`, `Option(id, text, correct: bool, tags: frozenset[str], first_burn: int|None)`, `Question(id, type, prompt, options: list[Option], quip: str, status, needs_owner, fixed: str|None)`, `Tagline(id, text, status)`
  - `Content(names, questions, taglines)` with properties `approved_names`, `approved_questions` (excludes fixed), `first_burn_question`, `approved_taglines`, `tag_vocab: frozenset[str]` (union of approved name tags)
  - `load_content(dir: Path) -> Content` raises `ContentError(str)` listing every problem
  - `set_status(dir: Path, kind: Literal["names","questions","taglines"], id: str, status: str) -> None`
  - `append_entry(dir: Path, kind, entry: dict) -> None` (validates the result before writing; writes atomically via temp file + `os.replace`)
  - `BANNED_WORDS: frozenset[str]` (drug and political terms; a safety net, not the review)

- [ ] **Step 1: Failing tests** in `tests/test_content.py`:
  - `test_loads_sample`: `load_content(sample_content_dir)` has 20 names; `first_burn_question.id == "first_burn"`.
  - `test_only_approved_served`: add a `draft` name → not in `approved_names`.
  - `test_duplicate_ids_rejected`, `test_duplicate_name_text_case_insensitive_rejected`: `ContentError`.
  - `test_question_needs_a_correct_option`: `single` with no `correct` → `ContentError`; `first_burn` question exempt.
  - `test_option_tags_must_be_in_vocab`: option tag `zzz` not on any name → `ContentError`.
  - `test_banned_word_in_approved_rejected`: approved name containing a `BANNED_WORDS` term → `ContentError`; the same in `draft` → loads (owner sees it in review).
  - `test_rejects_unrenderable_chars`: name with emoji → `ContentError` (allowed: printable Latin-1 plus `’ “ ” – —`).
  - `test_invalid_yaml_raises_contenterror`: garbage file → `ContentError` naming the file.
  - `test_set_status_and_append_roundtrip`: approve a draft, append a name, reload, both present; file still valid YAML.
  - `test_append_invalid_does_not_write`: duplicate id → `ContentError`, file unchanged.
- [ ] **Step 2:** `pytest tests/test_content.py -q`. Expected: FAIL (import error).
- [ ] **Step 3:** Implement with `yaml.safe_load` / `yaml.safe_dump(sort_keys=False, allow_unicode=True)`.
- [ ] **Step 4:** `pytest tests/test_content.py -q`. Expected: PASS.
- [ ] **Step 5:** Add `python -m app.content <dir>` that prints counts by status/tag and errors (used by T6 and the owner). Commit `feat: content loader and validator`.

---

### Task 2: Name store (wave 1)

**Files:** Create `app/names.py`, `tests/test_names.py`

**Interfaces:**
- Consumes: `Name` from T1 (any object with `.id`, `.name`, `.tags`; tests use small stand-in dataclass if T1 is unmerged).
- Produces: `NameStore(db_path: Path)` with
  - `set_pool(names: Sequence[Name]) -> None` (swap the approved list; used rows untouched)
  - `pick(guest_tags: set[str], rng: random.Random) -> tuple[str, str]` → `(name_id, display_name)`; records it as used
  - `remaining() -> int`, `remaining_by_tag() -> dict[str,int]`
  - fallback ids are `stranger-<n>` with display `Dusty Stranger #<n>` (n = count of strangers + 1)

Algorithm (owner rule, exact):

```python
with self._lock, self._db:                        # one transaction
    used = {row[0] for row in db.execute("SELECT name_id FROM used")}
    unused = [n for n in self._pool if n.id not in used]
    matching = [n for n in unused if n.tags & guest_tags]
    choice = rng.choice(matching or unused) if unused else None
    # choice None → stranger fallback
    db.execute("INSERT INTO used(name_id, display, given_at) VALUES (?,?,?)", ...)
```

- [ ] **Step 1: Failing tests:**
  - `test_never_repeats`: pool of 50, 50 picks → 50 distinct ids; 51st → `stranger-1`, display `Dusty Stranger #1`.
  - `test_matching_tag_always_wins`: pool `{gnome×3, dust×3}`, guest `{"gnome"}`, 3 picks → all gnome.
  - `test_falls_back_to_random_when_category_empty`: after 3 gnome picks, 4th with `{"gnome"}` → a dust name.
  - `test_multiple_tags_pool_together`: guest `{"gnome","dust"}`, rng seeded, picks drawn from union.
  - `test_survives_restart`: pick, new `NameStore` on same file → `remaining()` reduced, name not re-picked.
  - `test_pool_swap_keeps_used`: `set_pool` with a superset → previously used still excluded.
  - `test_concurrent_picks_unique`: 8 threads × 10 picks on pool of 80 → 80 unique.
- [ ] **Step 2:** Run, expect FAIL. **Step 3:** Implement (`sqlite3`, `check_same_thread=False`, `threading.Lock`). **Step 4:** Run, expect PASS.
- [ ] **Step 5:** Commit `feat: never-repeat name store with tag matching`.

---

### Task 3: Badge renderer (wave 1)

**Files:** Create `app/badge.py`, `app/assets/fonts/` (two OFL fonts + their `OFL.txt`), `tests/test_badge.py`, `tools/preview_badges.py`

**Interfaces:**
- Produces: `render_badge(name: str, tagline: str, first_burn: int | None, qr_url: str | None = None, border: str | None = None, rng: random.Random | None = None) -> PIL.Image.Image`. `qr_url` → a ~20 mm QR (segno, error level M, no dithering) in the bottom-right corner with a tiny "your photo" caption; the name box shrinks to stay clear of it → mode `"1"`, size `(954, 672)`. `BORDERS: tuple[str, ...]` with ≥3 procedural styles; `None` = random choice.
- First-burn line copy: `f"First Burn: Baby Burn {n}"`; omitted when `None`.
- Layout knobs as module constants: `MARGIN_PX`, `NAME_BOX` (rect), `NAME_MAX_PT`, `NAME_MIN_PT`, `NAME_MAX_LINES = 2`.

- [ ] **Step 1: Failing tests:**
  - `test_size_and_mode`: `(954, 672)`, `"1"`.
  - `test_long_name_fits`: 40-char name → the font size chosen ≥ `NAME_MIN_PT` and bbox of text inside `NAME_BOX` (expose `fit_name(name) -> (font_pt, lines)` for the test).
  - `test_short_name_bigger_than_long`: `fit_name("Gnome")[0] > fit_name("Captain Moist Towelette of Hole Foods")[0]`.
  - `test_each_border_draws_ink_on_edges`: for each style, the outer 30-px frame has black pixels.
  - `test_no_first_burn_line_when_none`: renders without error and differs from `first_burn=2`.
  - `test_qr_drawn_clear_of_name`: with `qr_url="https://photos.example.com/p/aaaaaaaaaaaaaaaaaaaaaa"`, the bottom-right region has ink, and `fit_name` for a 40-char name still fits (QR doesn't overlap `NAME_BOX`). Decoding is checked by eye/phone in T11.
- [ ] **Step 2:** Run, FAIL. **Step 3:** Implement: binary-search font size with word-wrap ≤2 lines; borders drawn with `ImageDraw` (e.g. sun rays, dust dots, ticket notches). Render in `"L"`, threshold to `"1"` (no dithering for text).
- [ ] **Step 4:** Run, PASS. `python tools/preview_badges.py out/` writes PNGs for every sample name; eyeball them.
- [ ] **Step 5:** Commit `feat: badge renderer for DK-1234`.

---

### Task 4: Printer (wave 1)

**Files:** Create `app/printer.py`, `tests/test_printer.py`, `tools/test_print.py`

**Interfaces:**
- Produces: `class PrintError(Exception)`; `make_printer(target: str, out_dir: Path) -> Callable[[Image.Image], None]`.
  - `target == "file"` → writes `out_dir/badge-<timestamp>.png` (dev and tests).
  - `target.startswith("usb://")` → `brother_ql.conversion.convert(BrotherQLRaster("QL-800"), [img], label="60x86", rotate="90", threshold=70, dither=False, cut=True)` then `send(..., backend_identifier="pyusb")`; any exception or a send result without `did_print` → `PrintError` with the reason.
  - One printer call at a time (module lock).

- [ ] **Step 1: Failing tests:** `test_file_printer_writes_png`; `test_usb_failure_raises_printerror` (monkeypatch `send` to raise and to return `{"did_print": False}` → `PrintError`); `test_convert_called_with_60x86_and_rotate` (monkeypatch `convert`, assert kwargs).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** `tools/test_print.py "Lil' Gnomesayer"` renders + prints (used on hardware in T11). Commit `feat: QL-800 printer wrapper`.

---

### Task 5: Photo transform providers + bake-off (wave 1)

**Files:** Create `app/transform.py`, `tests/test_transform.py`, `tools/bakeoff.py`

**Interfaces:**
- Produces:
  - `DUSTY_PROMPT: str`: the shared prompt (starting point: turn the person into a dusty, goggled, playa-weathered *baby* version of themselves wearing their actual costume, cute not creepy, keep their face recognizable, warm sunset desert light).
  - `class Provider(Protocol)`: `transform(jpeg: bytes) -> bytes` (JPEG out) and `tag(jpeg: bytes, vocab: Collection[str]) -> set[str]`.
  - `get_provider(name: str, cfg: Config) -> Provider` for `openai`, `gemini`, `fake`.
  - `FakeProvider(delay_s=3.0)`: `transform` returns the input after `delay_s`; `tag` returns `set()`.
  - `parse_tags(text: str, vocab) -> set[str]`: accepts a JSON array or comma list, lowercases, keeps only vocab members, never raises.
- OpenAI: `client.images.edit(model=cfg.openai_image_model, image=..., prompt=DUSTY_PROMPT)` → b64 decode; tag via `client.responses.create` with `input_image` and a prompt listing `vocab`, asking for a JSON array.
- Gemini: `genai.Client().models.generate_content(model=cfg.gemini_image_model, contents=[DUSTY_PROMPT, image_part])` → first `inline_data`; tag via `cfg.gemini_vision_model`.
- **Verify the current model IDs and SDK call shapes in each provider's docs on the day you implement**; put them in `Config` defaults.

- [ ] **Step 1: Failing tests:** `test_parse_tags_filters_vocab` (`'["gnome","banana"]'` → `{"gnome"}`), `test_parse_tags_garbage` (`"lol"` → `set()`), `test_fake_provider_roundtrip`, `test_openai_transform_decodes_b64` and `test_gemini_transform_returns_inline_data` (monkeypatched clients), `test_unknown_provider_raises`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5:** `tools/bakeoff.py PHOTOS_DIR OUT_DIR [--prompt-file f]`: for each photo, both providers sequentially. Record seconds and errors. Write `OUT_DIR/index.html` with columns original | OpenAI | Gemini, latency under each, and a per-provider cost line from constants `COST_PER_IMAGE_USD` (filled from current pricing pages). Smoke-run with `--providers fake`. Commit `feat: transform providers and bake-off tool`.

---

### Task 6: Content drafting (wave 1, no code; output goes to the PRIVATE repo)

**Files:** Create `content/names.yaml`, `content/questions.yaml`, `content/taglines.yaml` in the private `baby-burn-content` clone.

**Inputs:** private `content/SEEDS.md` (owner seeds + Baby Burn inside jokes), `docs/research/lore.md`, schema from T0.

- [ ] **Step 1:** Pick the tag vocabulary (~12–15 tags), e.g. `gnome, dust, sauna, thunderdome, latex, hole-foods, drone-show, art-car, temple, brag, baby, fur, sparkle, mutant, porta`. There is no vocab file: T1 derives the vocab from the tags on approved names, so every tag used on a quiz option must also appear on ≥ 1 name.
- [ ] **Step 2:** Draft **≥ 360 names**, all `status: draft`. Size each tag's share by how many quiz options/costume looks point to it (popular tags like `gnome`, `dust`, `sparkle`, `fur` get ≥ 35 each); every name ≥1 tag. Mix classic playa-name patterns with miniature/"Lil'"/"Baby" twists. Flag sex/body humor `needs_owner: true`.
- [ ] **Step 3:** Draft **≥ 45 questions** (≥ 35 `single`, ≥ 8 `multi`), including every owner seed question in `SEEDS.md` verbatim. Each option ≤ 45 chars, ≤ 4 options, a `quip` ≤ 60 chars, tags on options where natural.
- [ ] **Step 4:** Draft **≥ 30 taglines** ≤ 32 chars.
- [ ] **Step 5:** `python -m app.content content/` passes with zero errors (after T1 merges; until then, hand-check against the schema). No real people's names, no drugs, no politics. Commit and push to the private repo only.

---

### Task 7: Pi provisioning and ops docs (wave 1)

**Files:** Create `deploy/setup-pi.sh`, `deploy/babyburn.service`, `deploy/99-brother-ql.rules`, `docs/ops.md`

**Interfaces:**
- Consumes: `python -m app.web` entry point (T9) serving HTTPS on `:443` using `certs/babyburn.local.pem` + `-key.pem`.
- Produces: an idempotent setup script and a go-live checklist.

- [ ] **Step 1:** `setup-pi.sh` (run as the `pi` user with sudo): apt `python3-venv libusb-1.0-0 avahi-daemon git`; set hostname `babyburn`; venv + `pip install .`; udev rule for USB vendor `04f9` product `209b` (verify with `lsusb`), mode 0666; generate certs with `mkcert babyburn.local` (mkcert CA also exported to `certs/rootCA.pem` for the iPad); install and enable the systemd unit (`Restart=always`, `EnvironmentFile=/home/pi/baby-burn-name-generator/.env`, `AmbientCapabilities=CAP_NET_BIND_SERVICE`).
  Also, **Cloudflare R2 automation** (owner has no Cloudflare account yet, so automate everything possible):
  - `deploy/setup-r2.sh` (runs on the owner's Mac, idempotent, uses `npx wrangler`): `wrangler login` (opens the browser), `wrangler r2 bucket create baby-burn-photos`, `wrangler r2 bucket dev-url enable baby-burn-photos` (public `https://pub-….r2.dev` URL, no listing), `wrangler r2 bucket lifecycle add baby-burn-photos --prefix tmp/ --expire-days 14` (the only rule; nothing on `p/` or `album/`). It then prints the dev URL and the account ID, and walks the owner through the one manual step: dashboard → R2 → Manage API tokens → "Object Read & Write" scoped to `baby-burn-photos` only. It prompts for the Access Key ID and Secret, generates `ALBUM_SECRET`, and writes `PHOTO_BUCKET`, `PHOTO_BASE_URL`, `S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com`, `S3_REGION=auto`, the keys and `ALBUM_SECRET` into `.env`. Finally it runs `tools/cloud_check.py`. Verify every wrangler subcommand and flag against the current wrangler docs when implementing.
  - `docs/ops.md` → "Cloudflare from zero": a click-by-click section with sign-up, email verification, enabling R2 + adding a payment method, running the script, and creating the token. Note: the free tier (10 GB, free egress) easily covers ~200 guests; `r2.dev` URLs are rate-limited and Cloudflare calls them non-production, which is fine for one party (a custom domain would be the upgrade).
  - `tools/cloud_check.py`: puts a test page with `put_object`, prints its URL, waits for Enter, then deletes it. `tools/album_reset.py`: lists and deletes `album/<secret>/` objects and clears `AlbumIndex`, only after typing `yes`.
- [ ] **Step 2:** `shellcheck deploy/setup-pi.sh`. Expected: no errors.
- [ ] **Step 3:** `docs/ops.md`:
  - Ethernet-first networking.
  - Hotspot fallback: `nmcli dev wifi hotspot ifname wlan0 ssid BABYBURN password …`, noting it means no cloud transform.
  - iPad: install `rootCA.pem` profile, then trust it in Settings → General → About → Certificate Trust Settings; open `https://babyburn.local`; Add to Home Screen; Guided Access.
  - QL-800 Editor Lite off.
  - Go-live checklist from the spec, plus `python -m app.content content/` showing approved counts, and a test print.
  - Cloud check from a phone on cellular: `python tools/cloud_check.py` → open the printed URL on the phone → page loads; then the bucket root URL → no listing.
  - Post-event: copy `data/album/` off the Pi (backup of shared photos). Unshared photos vanish 14 days after upload (lifecycle on `tmp/`); album photos stay until the owner deletes the `album/` prefix.
- [ ] **Step 4:** Commit `docs: Pi setup, service, ops checklist`.

---

### Task 8: Kiosk front end (wave 1; built against the API contract in Task 9)

**Files:** Create `app/static/kiosk.html`, `app/static/kiosk.js`, `app/static/kiosk.css`

**Interfaces:**
- Consumes: Task 9 API contract (below), exactly.

Screens and behavior:
**Skip rule:** every screen below has a visible Skip control (≥ 64 px, secondary style). Skips never block or error.

1. **Start:** big "Get your playa name" button + the line "Your photo goes to an AI to turn you into a dusty baby."
2. **Snap:** `getUserMedia({video:{facingMode:"user"}})` live preview, one big shutter with 3-2-1 countdown. Downscale to max 1024 px JPEG q=0.85 via canvas, then `POST /api/session`. "Skip photo" is always shown (and is the only option if the camera is unavailable or denied) → `POST /api/session` with no file.
3. **Quiz:** fetch `/api/quiz`; render `first_burn` then 4 questions with a progress dots row. `single`: tap → 1 s flash (green/red + `quip`) → next. `multi`: tap toggles, "Done" → 1 s flash. Each question has "Skip" (no answer sent). A "Just give me a name" link on every quiz screen jumps straight to Reveal with the answers so far.
4. **Reveal:** `POST /api/session/{id}/finish`. Show `name` huge, `tagline`, `first_burn_label`. If `printed` is false, show "Badge jammed! Grab a volunteer." Poll `GET /api/session/{id}/photo` every 1 s. `200` → show image. `{"status":"failed"}` or 30 s elapsed → show the original snapshot (kept in memory) with class `dusty` (CSS `filter: sepia(.7) contrast(1.15) saturate(.8)` plus a grain overlay).
5. **Take it home** (only if a photo was taken): the QR from `GET /api/session/{id}/qr.svg` shown large with "Scan to get your dusty baby photo (deletes in 14 days)", plus buttons "Share to the Baby Burn album (keeps it)" → `POST /api/session/{id}/photo-choice {"choice":"album"}`, "Delete my photo" → `{"choice":"delete"}`, and "Done" → `{"choice":"done"}` → thank-you → Start. Idle timeout counts as Done.
- Any screen: 20 s with no touch → back to Start. All `fetch` calls go through `api(path, opts)`, which retries 3× with 0.5/1/2 s backoff on network errors or 5xx.
- iPad landscape, tap targets ≥ 64 px, playa palette, no external fonts or CDNs (offline-safe).

- [ ] **Step 1:** Build all screens against a stub (`kiosk.js` has `const STUB = location.search.includes("stub")` returning canned API responses).
- [ ] **Step 2:** Open `kiosk.html?stub` in Chrome with iPad viewport; click through the full flow; time it (target < 30 s for a decisive guest); verify the 20 s idle reset, the camera-denied path, and the all-skips path (Start → Skip photo → Just give me a name → Reveal) in < 8 s.
- [ ] **Step 3:** Commit `feat: kiosk front end`. (Live-API check happens in Task 10's wave.)

---

### Task 9: Kiosk API, sessions, app factory (wave 2)

**Files:** Create `app/web.py`, `app/sessions.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `Config` (T0), `load_content`/`Content` (T1), `NameStore` (T2), `render_badge` (T3), `make_printer`/`PrintError` (T4), `get_provider` (T5).
- Produces:
  - `create_app(cfg: Config, *, printer=None, provider=None, now=time.monotonic) -> Flask`; stores `AppState` at `app.extensions["babyburn"]`.
  - `AppState` fields: `cfg`, `content: Content`, `content_error: str | None`, `names: NameStore`, `printer`, `provider_name: str`, `provider: Provider`, `sessions: SessionStore`, `photos: PhotoStore`, `cloud: CloudPhotos`, `recent_badges: deque[(name_id, display, tagline, first_burn)]` (max 50), and `reload_content() -> None` (on `ContentError` keep the old content and set `content_error`).
  - `SessionStore(executor, ttl_s, now)`: `create(photo: bytes | None, provider, vocab) -> Session`, `get(id) -> Session | None`, `expire() -> None` (drops sessions older than ttl, deleting photo bytes).
  - `Session`: `id`, `token: str | None` (None when no photo), `transform_future`, `tag_future`, `result` (cached finish payload), `created_at`, `finished_at`.
  - `PhotoStore(data_dir)`: local only: `save_original(token, jpeg)`, `save_dusty(token, jpeg)` (transform done-callback), `paths(token) -> (orig, dusty|None)`, `copy_to_album_backup(token, name_id)` (→ `data/album/`), `delete(token)`.
  - `CloudPhotos(client, bucket: str, base_url: str, album_secret: str, album: AlbumIndex, executor, now=datetime.now)`: `url(token)` (= `f"{base_url}/p/{token}"`), `album_url()`, `upload(token, *, original=None, dusty=None) -> Future` (puts images under `tmp/<token>/` (or `album/…` if already shared) with `ContentType=image/jpeg` and `ContentDisposition=attachment; filename="baby-burn-dusty.jpg"` / `...-original.jpg`, then re-puts the page `p/<token>` with `ContentType=text/html`, `CacheControl=no-cache`), `share(token, playa_name) -> Future`, `delete(token) -> Future`, `errors: int`. `client` is `boto3.client("s3", endpoint_url=..., region_name=..., config=Config(retries={"mode": "standard", "max_attempts": 5}))`; tests pass a fake with `put_object` / `copy_object` / `delete_objects`. Ops on one token run in order (per-token lock).
  - `render_photo_page(token: str, *, state: Literal["temp","album","deleted"], has_dusty: bool, uploaded_at: datetime, retention_days: int = 14, album_url: str) -> str`: self-contained mobile HTML (inline CSS/JS, no external assets). **Top of page, large type: the deletion status**: `temp` → live JS countdown to `uploaded_at + 14 d` ("This photo self-destructs in 13 days, 4 hours, on or after Sat Nov 28"); `album` → "Shared to the Baby Burn album. No expiry."; `deleted` → "This photo was deleted." Then the dusty image (if `has_dusty` is false: "still developing…" and a JS retry every 3 s for up to 2 min), the original image, a Download button for each, and a link to the album. If an image 404s after the countdown has passed (expired by lifecycle), JS swaps the images for "This photo self-destructed."
  - `render_album_page(entries: list[tuple[token, playa_name]], base_url) -> str`: grid of shared dusty photos with playa-name captions, tap for original; "Baby Burn 3 album" header.
  - `AlbumIndex(db_path)` (SQLite table `album(token PRIMARY KEY, playa_name, added_at)`): `add`, `remove`, `entries()`; the source of truth for re-rendering the album page.

  - `python -m app.web` runs the kiosk app on `0.0.0.0:443` (TLS, `threaded=True`). A background thread runs `sessions.expire()` every 60 s (deletes local photos of ended or abandoned sessions; album backups in `data/album/` are kept). *ponytail: Flask dev server is fine for one kiosk; switch to gunicorn if more clients appear.*

API contract (exact):

| Method & path | Request | Response |
|---|---|---|
| `POST /api/session` | multipart `photo` (optional) | `{"session_id": str, "has_photo": bool}`; original saved to `PhotoStore` and `cloud.upload(token, original=...)` started immediately |
| `GET /api/quiz` | - | `{"questions":[{"id","type","prompt","quip","options":[{"id","text","correct","first_burn"}]}]}`: first is `first_burn`, then `quiz_random_count` random approved |
| `POST /api/session/<id>/finish` | `{"answers":[{"question_id","option_ids":[...]}]}` (may be empty; unknown or skipped questions are ignored) | `{"name","tagline","first_burn_label": str\|null,"printed": bool,"photo_url": str\|null}` |
| `GET /api/session/<id>/qr.svg` | - | `image/svg+xml` QR of `photo_url`; 404 if no photo |
| `GET /api/session/<id>/photo` | - | `200 image/jpeg` · `202 {"status":"pending"}` · `200 {"status":"failed"}` |
| `POST /api/session/<id>/photo-choice` | `{"choice": "album"\|"delete"\|"done"}` | `{"ok": true}` |
| unknown/expired session | - | `404 {"error":"expired"}` |

Finish logic: guest tags = union of chosen options' `tags` ∪ costume tags (only if `tag_future.done()`, never wait). `first_burn` comes from the chosen option on the fixed question. Then `names.pick` → random approved tagline → `render_badge` → printer. `PrintError` → `printed:false` (name kept; badge added to `recent_badges` for reprint). The result is cached on the session, so a repeat finish returns it without picking or printing again.

Photo endpoint: transform future done → JPEG. Exception, or older than `transform_timeout_s` → `failed`.

Photo choice: `album` → `cloud.share(token, playa_name)` (copy `tmp/`→`album/<secret>/`, delete `tmp/`, re-put page as `album`, `AlbumIndex.add`, re-put album page) + `PhotoStore.copy_to_album_backup`. A dusty image landing after sharing goes straight to `album/`. `delete` → `cloud.delete(token)` (tmp + album images, page re-put as `deleted`, `AlbumIndex.remove`, album page re-put) + `PhotoStore.delete(token)`. `done` → nothing (local copy goes at session end; cloud copy at 14 days). Otherwise the transform done-callback calls `cloud.upload(token, dusty=...)`. `photo_url = cloud.url(token)`. The badge gets `qr_url=photo_url` when the session has a photo.

- [ ] **Step 1: Failing tests** (Flask test client, `printer="file"` into `tmp_path`, `FakeProvider(delay_s=0)`):
  - `test_full_flow`: session → quiz (5 questions, first is `first_burn`) → finish → name non-empty, a PNG in the print dir, `first_burn_label == "First Burn: Baby Burn 2"` for option `bb2`.
  - `test_finish_is_idempotent`: two finishes → same name, exactly one PNG, `remaining()` dropped by 1.
  - `test_answer_tags_steer_name`: answer an option tagged `gnome` → name has `gnome` tag (sample content has gnome names).
  - `test_print_failure_keeps_name`: printer raising `PrintError` → `printed:false`, name present, in `recent_badges`.
  - `test_photo_failed_on_provider_error` and `test_photo_failed_after_timeout` (injected `now`).
  - `test_choice_album_moves_to_album_prefix`: fake S3 has `album/<secret>/<token>/original.jpg`, no `tmp/<token>/*`, the page mentions "No expiry", and the album page lists the playa name. / `test_dusty_after_share_goes_to_album`. / `test_choice_delete_removes_everywhere` (after sharing, then deleting: no `tmp/` or `album/` keys for the token, album page no longer lists it, page says deleted, local dir gone). / `test_choice_done_keeps_tmp_and_countdown`: page contains the countdown script and the expiry date = upload + 14 days.
  - `test_session_uploads_original_and_page`: after `POST /api/session` with photo and draining the executor, fake S3 has `p/<token>` (`text/html`, mentions `original.jpg` and the countdown) and `tmp/<token>/original.jpg`.
  - `test_dusty_uploaded_when_transform_done`: page re-put and `tmp/<token>/dusty.jpg` present.
  - `test_upload_failure_does_not_block_finish`: fake S3 raising on every put → finish returns name, `printed: true`, `cloud.errors > 0`.
  - `test_photo_page_states`: `temp` shows the countdown and the "on or after" date; `album` shows "No expiry" and no countdown; `deleted` shows no images; `has_dusty=False` adds "still developing" and the retry script.
  - `test_no_photo_no_upload`: session without photo → fake S3 untouched.

  - `test_skip_everything`: session without photo + finish with `answers: []` → name returned, badge printed with no first-burn line and no QR, `photo_url` null, `qr.svg` → 404.
  - `test_badge_has_qr_when_photo`: finish with photo → render_badge called with `qr_url == photo_url` (spy).
  - `test_tokens_unguessable`: 100 sessions → 100 distinct tokens, each ≥ 22 chars.
  - `test_abandoned_session_expires`: advance `now` past ttl, `expire()`, then `GET photo` → 404, local photo dir gone; `remaining()` unchanged.
  - `test_session_without_photo`: no file → finish works; photo → `failed`.
  - `test_only_approved_questions_served`: draft question never appears across 30 quiz calls.
- [ ] **Step 2–4:** FAIL → implement → PASS. Run full suite `pytest -q`.
- [ ] **Step 5:** Run locally `TRANSFORM_PROVIDER=fake python -m app.web` (self-signed via mkcert on the Mac), click through `kiosk.html` in Chrome. Commit `feat: kiosk API and sessions`.

---

### Task 10: Admin (wave 3)

**Files:** Create `app/admin.py` (Flask blueprint, registered by `create_app`), `app/static/admin.html`, `tests/test_admin.py`

**Interfaces:**
- Consumes: `AppState` (T9), `set_status`/`append_entry`/`load_content` (T1), `render_badge` (T3).
- Produces (all behind HTTP Basic, password = `ADMIN_PIN`):
  - `GET /admin`: dashboard: names remaining (total + by tag, red under `low_names_warning`), approved/draft/rejected counts per kind, `content_error` banner, provider switcher, recent badges with Reprint buttons, test-print button.
  - `GET /admin/review?kind=names&status=draft`: list with Approve/Reject buttons; `needs_owner` rows highlighted; names show a badge preview thumbnail.
  - `POST /admin/status` `{kind,id,status}` → `set_status` then `reload_content`.
  - `POST /admin/add` `{kind, ...fields}` → `append_entry` with `status: approved`, then reload (form fields for name/tags, question/options with correct checkboxes, tagline).
  - `GET /admin/preview/<name_id>.png`: `render_badge` with a sample tagline and `first_burn=3`.
  - `POST /admin/reprint` `{index}` · `POST /admin/test-print` · `POST /admin/provider` `{name}` (runtime only; `.env` stays the default).
  - Dashboard also shows the album link (`cloud.album_url()`), album count, and `cloud.errors`.

- [ ] **Step 1: Failing tests:**
  - `test_requires_pin`: no auth → 401 on `/admin`, `/admin/review`, `/admin/preview/x.png`.
  - `test_approve_makes_name_pickable`: approve a draft name, then `names.set_pool` reflects it.
  - `test_add_question_appears_in_quiz`.
  - `test_reload_failure_keeps_old_content`: corrupt `names.yaml` then `POST /admin/status` → error shown, `/api/quiz` still works.
  - `test_reprint`: failing printer then fixed printer; reprint index 0 → PNG written.
- [ ] **Step 2–4:** FAIL → implement → PASS (`pytest -q` full suite).
- [ ] **Step 5:** Owner review dry run on the Mac: `CONTENT_DIR=content python -m app.web`, open `/admin/review`. Wire-check `kiosk.html` against the live API (full flow, double-tap Finish, kill provider mid-flow). Commit `feat: admin review, add, reprint`.

---

### Task 11: Hardware bring-up and rehearsal (wave 4, with owner, needs SSH to the Pi)

- [ ] **Step 1:** On the Pi: clone the repo, clone the private content into `content/`, fill `.env`, run `deploy/setup-pi.sh`. `systemctl status babyburn` shows active.
- [ ] **Step 2:** `lsusb` shows `04f9:209b` (fix the udev rule if the product ID differs). `python tools/test_print.py "Lil' Gnomesayer"` prints. Check orientation, margins and border clipping on a real label, then tune `MARGIN_PX`/`NAME_BOX`/`threshold` (the calibration knobs). Print one badge of the longest approved name.
- [ ] **Step 3:** Phones: scan the on-screen QR and a printed badge QR with an iPhone and an Android, on house Wi-Fi **and** on cellular; both open the photo page and save the image. iPad: trust the cert, open `https://babyburn.local`, Guided Access on. Time 5 full guest runs (target < 30 s each). Confirm the transform shows on the reveal with the chosen provider.
- [ ] **Step 4:** Failure drills:
  - unplug the printer mid-flow (expect "grab a volunteer", then reprint from admin)
  - pull Ethernet (expect the dust-filter fallback; badge still prints)
  - `sudo reboot` (service returns; no name repeats)
  - corrupt then restore a YAML file
- [ ] **Step 5:** Soak: 30 back-to-back runs. Every printed name is unique, and `/admin` counts match. Reset the name DB (`rm data/names.sqlite3` *with owner confirmation*), and remove rehearsal photos from the album before the event (`tools/album_reset.py`, with owner confirmation). Tag `v1.0-babyburn3`.
