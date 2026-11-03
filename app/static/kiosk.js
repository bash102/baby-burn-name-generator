"use strict";
// ---- All guest-facing text lives here. Edit freely. ----
const COPY = {
  startTitle: "Get your playa name",
  startSub: "Your photo goes to an AI to turn you into a dusty baby.",
  startSkipPhoto: "Skip the photo, just play",
  snapTitle: "Say cheese (dust optional)",
  snapShutter: "Snap it!",
  skipPhoto: "Skip photo",
  camDenied: "No camera, no problem. The playa is full of mysteries.",
  quizSkip: "Skip",
  quizDone: "Done",
  justName: "Just give me a name",
  multiHint: "Pick all that apply, then tap Done.",
  right: "Correct!",
  wrong: "Nope!",
  noted: "Noted.",
  revealing: "Consulting the dust oracle...",
  revealIntro: "Your playa name is",
  jammed: "Badge jammed! Grab a volunteer.",
  developing: "Developing your dusty baby...",
  takeHome: "Take it home",
  skip: "Skip",
  qrScan: "Scan to get your dusty baby photo (deletes in 14 days)",
  album: "Share to the Baby Burn album (keeps it)",
  delete: "Delete my photo",
  done: "Done",
  thanks: "Thanks for burning with us!",
  deleteFailed: "Couldn't delete your photo. Please try again.",
  tryAgain: "Try again",
  thanksSub: "Hydrate. Stay dusty.",
  error: "The dust got in the gears. Tap to start over.",
};
// Idle reset is per screen: IDLE_MS everywhere, IDLE_LONG_MS on Reveal and Take it home. Reveal auto-advances after REVEAL_ADVANCE_MS if there is a photo.
const IDLE_MS = 20000, IDLE_LONG_MS = 45000, REVEAL_ADVANCE_MS = 8000, POLL_FETCH_MS = 5000;
const FLASH_MS = 1000, PHOTO_TIMEOUT_MS = 30000, POLL_MS = 1000, COUNT_FROM = 3, MAX_PX = 1024;
const STUB = location.search.includes("stub");   // ?stub = canned API; ?stub=fail photo fails; ?stub=slow photo never ready; &jam printer jam
const STUB_MODE = new URLSearchParams(location.search).get("stub");

// ---- API ----
const sleep = ms => new Promise(r => setTimeout(r, ms));
// Retries 3x (0.5/1/2 s) on network errors or 5xx; returns the last response, or throws the last error.
// Which calls retry: GETs (idempotent) and POST /finish and /photo-choice (the server dedupes per session).
// POST /api/session never retries: there is no session id yet, so a retry after a lost response could
// create a duplicate session and a second paid AI transform. The caller shows the error screen instead.
const once = (path, opts) => STUB ? stubApi(path, opts) : fetch(path, opts);
async function api(path, opts = {}) {
  const retries = path === "/api/session" ? 0 : 3;
  for (let i = 0; ; i++) {
    let res;
    try {
      res = await once(path, opts);
      if (res.status < 500 || i >= retries) return res;
    } catch (e) { if (i >= retries) throw e; }
    await sleep(500 * 2 ** i);
  }
}
const postJSON = (path, body) => api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// ---- Stub ----
const STUB_QUIZ = { questions: [
  { id: "first_burn", type: "single", prompt: "Which Baby Burn was your first?", quip: "", options: [
    { id: "bb1", text: "Baby Burn 1 (OG dust)", first_burn: 1 }, { id: "bb2", text: "Baby Burn 2", first_burn: 2 }, { id: "bb3", text: "This one! Ring the bell!", first_burn: 3 }] },
  { id: "q1", type: "single", prompt: "What goes in a Burning Man margarita?", quip: "Hydration is a cocktail.", options: [
    { id: "a", text: "Gatorade, a warm Tecate, a little water", correct: true }, { id: "b", text: "Tequila, lime, salt", correct: false }] },
  { id: "q2", type: "single", prompt: "Pick your ride for the playa.", quip: "If it doesn't blink, it doesn't exist.", options: [
    { id: "a", text: "A bike with too many blinky lights" }, { id: "b", text: "A sturdy wagon pulled by a gnome" }, { id: "c", text: "An art car shaped like a giant fish" }] },
  { id: "q3", type: "multi", prompt: "What belongs in your pack?", quip: "Always bring a spare dust mask.", options: [
    { id: "a", text: "Goggles", correct: true }, { id: "b", text: "Snacks", correct: true }, { id: "c", text: "A tuxedo", correct: false }] },
  { id: "q4", type: "single", prompt: "Where is the man?", quip: "Center. Always center.", options: [
    { id: "a", text: "Center playa", correct: true }, { id: "b", text: "Reno", correct: false }] },
] };
let stubPolls = 0;
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "Content-Type": "application/json" } });
async function stubApi(path, opts) {
  await sleep(120);
  const m = opts.method || "GET";
  if (path === "/api/session") return json({ session_id: "stub1", has_photo: !!(opts.body && opts.body.get("photo")) });
  if (path === "/api/quiz") return json(STUB_QUIZ);
  if (path.endsWith("/finish")) return json({ name: "Sir Glitters-a-Lot", tagline: "Certified dust goblin", first_burn_label: "First Burn: Baby Burn 2", printed: !location.search.includes("jam"), photo_url: "https://example.invalid/p/stub" });
  if (path.endsWith("/qr.svg")) return new Response('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 8 8"><path d="M0 0h3v3H0zM5 0h3v3H5zM0 5h3v3H0zM4 4h1v1H4zM6 5h2v1H6zM5 7h3v1H5z"/></svg>', { headers: { "Content-Type": "image/svg+xml" } });
  if (path.endsWith("/photo-choice") && STUB_MODE === "faildelete" && JSON.parse(opts.body).choice === "delete") return json({ error: "boom" }, 500);
  if (path.endsWith("/photo-choice")) { window.stubChoices = (window.stubChoices || []).concat(JSON.parse(opts.body).choice); return json({ ok: true }); }
  if (path.endsWith("/photo") && m === "GET") {
    if (STUB_MODE === "fail") return json({ status: "failed" });
    if (STUB_MODE === "slow" || ++stubPolls < 3) return json({ status: "pending" }, 202);
    const c = document.createElement("canvas"); c.width = c.height = 300;
    const g = c.getContext("2d"); g.fillStyle = "#c98"; g.fillRect(0, 0, 300, 300); g.fillStyle = "#654"; g.beginPath(); g.arc(150, 130, 70, 0, 7); g.fill();
    return new Response(await new Promise(r => c.toBlob(r, "image/jpeg")), { headers: { "Content-Type": "image/jpeg" } });
  }
  return json({ error: "expired" }, 404);
}

// ---- Tiny DOM helper ----
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v); else if (v != null) el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
}
const root = document.getElementById("screen");
const btn = (text, onclick, cls = "") => h("button", { class: cls, onclick, type: "button" }, text);

// ---- State / flow control ----
let S;           // per-guest state
let stream = null;
let epoch = 0;   // bumped on every screen change so stale async work is ignored
function stopCamera() { if (stream) { stream.getTracks().forEach(t => t.stop()); stream = null; } }
let idleMs = IDLE_MS;
function show(...kids) { epoch++; idleMs = IDLE_MS; root.replaceChildren(...kids.filter(Boolean)); bumpIdle(); }
const longIdle = () => { idleMs = IDLE_LONG_MS; bumpIdle(); };
let urls = [];   // object URLs for the current guest, revoked on reset
const objUrl = blob => { const u = URL.createObjectURL(blob); urls.push(u); return u; };
function newGuest() {
  urls.forEach(u => URL.revokeObjectURL(u)); urls = [];
  S = { id: null, hasPhoto: false, snapshot: null, quiz: null, qi: 0, answers: [], onTakeHome: false }; }

function toStart() {
  stopCamera(); newGuest();
  show(h("h1", {}, COPY.startTitle), h("p", {}, COPY.startSub),
    btn(COPY.startTitle, snapScreen, "big"), btn(COPY.startSkipPhoto, skipPhoto, "skip"));
}

// Idle: no touch for idleMs returns to Start (idle on the take-home screen counts as Done).
let idleT;
function bumpIdle() { clearTimeout(idleT); idleT = setTimeout(idle, idleMs); }
function idle() { if (S.onTakeHome) choose("done"); toStart(); }
addEventListener("pointerdown", bumpIdle, true);

// ---- Snap ----
async function createSession(blob) {
  const fd = new FormData();
  if (blob) fd.append("photo", blob, "photo.jpg");
  const res = await api("/api/session", { method: "POST", body: fd });
  if (!res.ok) throw new Error("session " + res.status);
  const j = await res.json();
  S.id = j.session_id; S.hasPhoto = j.has_photo;
}
function fail() { stopCamera(); show(h("h2", {}, COPY.error), btn("OK", toStart, "big")); }
async function skipPhoto(ev) {
  if (ev && ev.currentTarget) ev.currentTarget.disabled = true;   // one tap only: a second would make a second session
  stopCamera(); const e = epoch;
  try { await createSession(null); } catch { return e === epoch ? fail() : null; }
  if (e === epoch) quizScreen();
}

async function snapScreen() {
  const video = h("video", { autoplay: "", playsinline: "", muted: "" });
  const count = h("div", { class: "count" });
  const shutter = btn(COPY.snapShutter, () => countdown(video, count, shutter), "big");
  shutter.disabled = true;   // enabled once the video is actually playing
  video.addEventListener("playing", () => { shutter.disabled = false; });
  show(h("h2", {}, COPY.snapTitle), h("div", { class: "cam" }, video, count),
    h("div", { class: "row" }, shutter, btn(COPY.skipPhoto, skipPhoto, "skip")));
  const e = epoch;
  try {
    const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user" } });
    if (e !== epoch) { s.getTracks().forEach(t => t.stop()); return; }
    stream = s; video.muted = true; video.srcObject = s; video.play().catch(() => {});
  } catch {
    if (e === epoch) show(h("h2", {}, COPY.camDenied), btn(COPY.skipPhoto, skipPhoto, "big"));
  }
}
async function countdown(video, count, shutter) {
  shutter.disabled = true; const e = epoch;
  for (let n = COUNT_FROM; n > 0; n--) { count.textContent = n; await sleep(1000); if (e !== epoch) return; }
  count.textContent = "";
  const vw = video.videoWidth || 640, vh = video.videoHeight || 480, scale = Math.min(1, MAX_PX / Math.max(vw, vh));
  const c = h("canvas"); c.width = Math.round(vw * scale); c.height = Math.round(vh * scale);
  c.getContext("2d").drawImage(video, 0, 0, c.width, c.height);
  const blob = await new Promise(r => c.toBlob(r, "image/jpeg", 0.85));
  stopCamera();
  if (e !== epoch) return;
  S.snapshot = objUrl(blob);
  try { await createSession(blob); } catch { return e === epoch ? fail() : null; }
  if (e === epoch) quizScreen();
}

// ---- Quiz ----
async function quizScreen() {
  if (!S.quiz) {
    try { const r = await api("/api/quiz"); if (!r.ok) throw 0; S.quiz = (await r.json()).questions; } catch { return finish(); }
  }
  if (S.qi >= S.quiz.length) return finish();
  const q = S.quiz[S.qi], picked = new Set();
  let locked = false;
  const answer = () => {
    if (locked) return; locked = true;
    S.answers.push({ question_id: q.id, option_ids: [...picked] });
    flash(q, picked);
  };
  const opts = h("div", { class: "opts" }, q.options.map(o => {
    const b = btn(o.text, () => {
      if (locked) return;
      if (q.type === "multi") { picked.has(o.id) ? picked.delete(o.id) : picked.add(o.id); b.classList.toggle("on"); }
      else { picked.add(o.id); answer(); }
    });
    return b;
  }));
  show(h("div", { class: "dots" }, S.quiz.map((_, i) => h("i", { class: i < S.qi ? "done" : i === S.qi ? "now" : "" }))),
    h("h2", {}, q.prompt), q.type === "multi" ? h("p", { class: "small" }, COPY.multiHint) : null, opts,
    h("div", { class: "row" },
      q.type === "multi" ? btn(COPY.quizDone, answer) : null,
      btn(COPY.quizSkip, () => { if (!locked) { locked = true; S.qi++; quizScreen(); } }, "skip"),
      btn(COPY.justName, finish, "link")));
}
function flash(q, picked) {
  const right = new Set(q.options.filter(o => o.correct).map(o => o.id));
  let kind = "meh", mark = "", label = COPY.noted;
  if (right.size) {
    const ok = picked.size === right.size && [...picked].every(id => right.has(id));
    kind = ok ? "ok" : "bad"; mark = ok ? "✓" : "✗"; label = ok ? COPY.right : COPY.wrong;
  }
  root.append(h("div", { class: "flash " + kind }, h("div", { class: "mark" }, mark), h("div", { class: "quip" }, label), h("div", { class: "quip" }, q.quip || "")));
  const e = epoch;
  setTimeout(() => { if (e === epoch) { S.qi++; quizScreen(); } }, FLASH_MS);
}

// ---- Reveal ----
async function finish() {
  show(h("h2", {}, COPY.revealing));
  const e = epoch;
  let r;
  try {
    if (!S.id) await createSession(null);
    const res = await postJSON(`/api/session/${S.id}/finish`, { answers: S.answers });
    if (!res.ok) throw new Error("finish " + res.status);
    r = await res.json();
  } catch { return e === epoch ? fail() : null; }
  if (e !== epoch) return;
  const photo = h("div", { class: "photo" }, COPY.developing);
  show(h("div", { class: "reveal" },
    h("div", { class: "txt" },
      h("p", {}, COPY.revealIntro), h("div", { class: "name" }, r.name), h("div", { class: "tagline" }, r.tagline),
      r.first_burn_label ? h("p", {}, r.first_burn_label) : null,
      r.printed ? null : h("div", { class: "warn" }, COPY.jammed)),
    S.hasPhoto ? photo : null),
    h("div", { class: "row" },
      S.hasPhoto ? btn(COPY.takeHome, takeHomeScreen, "big") : null,
      btn(S.hasPhoto ? COPY.skip : COPY.done, () => { if (S.hasPhoto) choose("done"); toStart(); }, "skip")));
  if (S.hasPhoto) {
    longIdle();
    const e2 = epoch;
    pollPhoto(photo, e2);
    setTimeout(() => { if (e2 === epoch) takeHomeScreen(); }, REVEAL_ADVANCE_MS);
  }
}
async function pollPhoto(box, e) {
  const t0 = Date.now();
  const useOriginal = () => {
    box.classList.add("grain"); box.replaceChildren(h("img", { class: "dusty", src: S.snapshot || "", alt: "" }));
  };
  while (e === epoch) {
    if (Date.now() - t0 > PHOTO_TIMEOUT_MS) return useOriginal();
    try {
      const ac = new AbortController(), t = setTimeout(() => ac.abort(), POLL_FETCH_MS);
      let blob, failed = false;
      try {
        const res = await once(`/api/session/${S.id}/photo`, { signal: ac.signal });
        if (res.status === 404) failed = true;   // expired
        else if ((res.headers.get("Content-Type") || "").startsWith("image/")) blob = await res.blob();
        else if (res.status === 200) failed = (await res.json()).status === "failed";
      } finally { clearTimeout(t); }
      if (blob) { if (e === epoch) box.replaceChildren(h("img", { src: objUrl(blob), alt: "" })); return; }
      if (failed) { if (e === epoch) useOriginal(); return; }
    } catch { /* keep polling until timeout */ }
    await sleep(POLL_MS);
  }
}

// ---- Take it home ----
async function choose(choice) {
  try { return (await postJSON(`/api/session/${S.id}/photo-choice`, { choice })).ok; } catch { return false; }
}
async function deletePhoto() {
  const e = epoch;
  if (await choose("delete")) return e === epoch ? thanks() : null;
  if (e !== epoch) return;
  const retry = btn(COPY.tryAgain, () => { lockChoices(); deletePhoto(); }, "big");
  const done = btn(COPY.done, () => { lockChoices(); choose("done"); thanks(); }, "skip");
  const lockChoices = () => { retry.disabled = done.disabled = true; };
  show(h("h2", {}, COPY.deleteFailed), h("div", { class: "row" }, retry, done));
}
function takeHomeScreen() {
  S.onTakeHome = true;
  const go = choice => { if (btns.some(b => b.disabled)) return; btns.forEach(b => { b.disabled = true; }); S.onTakeHome = false; if (choice === "delete") deletePhoto(); else { choose(choice); thanks(); } };
  const btns = [btn(COPY.album, () => go("album")), btn(COPY.delete, () => go("delete"), "skip"), btn(COPY.done, () => go("done"), "big")];
  const qr = h("img", { class: "qr", alt: "QR code" }), qrScan = h("p", {}, COPY.qrScan);
  const noQr = () => { qr.style.display = qrScan.style.display = "none"; };   // keep the buttons; just drop the QR area
  api(`/api/session/${S.id}/qr.svg`).then(r => { if (!r.ok) throw 0; return r.blob(); }).then(b => { qr.src = objUrl(b); }).catch(noQr);
  show(h("h2", {}, COPY.takeHome), qr,
    qrScan,
    h("div", { class: "row" }, ...btns));
  longIdle();
}
function thanks() {
  show(h("h1", {}, COPY.thanks), h("p", {}, COPY.thanksSub));
  const e = epoch;
  setTimeout(() => { if (e === epoch) toStart(); }, 3000);
}

toStart(); bumpIdle();
