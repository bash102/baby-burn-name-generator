import io
import re
import threading
from datetime import datetime, timedelta, timezone

import pytest
from PIL import Image

import app.web as web
from app.config import Config
from app.content import append_entry
from app.photos import MemoryS3, render_photo_page
from app.printer import PrintError
from app.transform import FakeProvider

BASE = "https://ph.example"
SECRET = "sekret"
T0 = datetime(2026, 11, 14, 18, 30, tzinfo=timezone.utc)


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 100, 50)).save(buf, "JPEG")
    return buf.getvalue()


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Kiosk:
    def __init__(self, tmp_path, content_dir, provider=None, printer=None, s3=None):
        self.cfg = Config(content_dir=content_dir, data_dir=tmp_path / "data", printer="file",
                          photo_bucket="b", photo_base_url=BASE, album_secret=SECRET)
        self.clock = Clock()
        self.s3 = s3 or MemoryS3()
        self.app = web.create_app(self.cfg, provider=provider or FakeProvider(delay_s=0),
                                  printer=printer, now=self.clock, s3=self.s3)
        self.state = self.app.extensions["babyburn"]
        self.state.cloud.now = lambda: T0
        self.c = self.app.test_client()

    def session(self, photo=True):
        data = {"photo": (io.BytesIO(jpeg()), "photo.jpg")} if photo else {}
        r = self.c.post("/api/session", data=data, content_type="multipart/form-data")
        assert r.status_code == 200, r.data
        return r.get_json()["session_id"]

    def sess(self, sid):
        return self.state.sessions.get(sid)

    def finish(self, sid, answers=()):
        r = self.c.post(f"/api/session/{sid}/finish", json={"answers": list(answers)})
        assert r.status_code == 200, r.data
        return r.get_json()

    def choose(self, sid, choice):
        r = self.c.post(f"/api/session/{sid}/photo-choice", json={"choice": choice})
        assert r.get_json() == {"ok": True}

    def drain(self):
        for s in list(self.state.sessions._sessions.values()):
            for f in (s.transform_future, s.tag_future):
                if f:
                    f.exception()
        self.state.cloud.drain()

    def keys(self, token=None):
        return {k for k in self.s3.objects if token is None or token in k}

    def page(self, token):
        return self.s3.objects[f"p/{token}"]["Body"].decode()

    def album_page(self):
        return self.s3.objects[f"album/{SECRET}/index.html"]["Body"].decode()

    def prints(self):
        return sorted((self.cfg.data_dir / "prints").glob("*.png"))


@pytest.fixture
def k(tmp_path, sample_content_dir):
    return Kiosk(tmp_path, sample_content_dir)


def test_full_flow(k):
    sid = k.session()
    qs = k.c.get("/api/quiz").get_json()["questions"]
    assert len(qs) == 5 and qs[0]["id"] == "first_burn"
    assert set(qs[1]) == {"id", "type", "prompt", "quip", "options"}
    assert all(set(o) == {"id", "text", "correct", "first_burn"} for q in qs for o in q["options"])
    r = k.finish(sid, [{"question_id": "first_burn", "option_ids": ["bb2"]}])
    assert r["name"] and r["tagline"] and r["printed"] is True
    assert r["first_burn_label"] == "First Burn: Baby Burn 2"
    assert r["photo_url"] == f"{BASE}/p/{k.sess(sid).token}"
    assert len(k.prints()) == 1


def test_preference_question_options_all_incorrect(k):
    for _ in range(10):
        for q in k.c.get("/api/quiz").get_json()["questions"]:
            if q["id"] == "ride":
                assert not any(o["correct"] for o in q["options"])
                return
    pytest.fail("ride never served")


def test_finish_is_idempotent(k):
    before = k.state.names.remaining()
    sid = k.session(photo=False)
    assert k.finish(sid) == k.finish(sid)
    assert len(k.prints()) == 1
    assert k.state.names.remaining() == before - 1


def test_answer_tags_steer_name(k):
    sid = k.session(photo=False)
    r = k.finish(sid, [{"question_id": "ride", "option_ids": ["b"]}, {"question_id": "nope", "option_ids": ["x"]}])
    gnomes = {n.name for n in k.state.content.approved_names if "gnome" in n.tags}
    assert r["name"] in gnomes


def test_print_failure_keeps_name(tmp_path, sample_content_dir):
    def jammed(img):
        raise PrintError("jam")

    k = Kiosk(tmp_path, sample_content_dir, printer=jammed)
    r = k.finish(k.session(photo=False))
    assert r["printed"] is False and r["name"]
    assert k.state.recent_badges[-1][1] == r["name"]


class Boom(FakeProvider):
    def transform(self, jpeg):
        raise RuntimeError("model down")


def test_photo_failed_on_provider_error(tmp_path, sample_content_dir):
    k = Kiosk(tmp_path, sample_content_dir, provider=Boom(delay_s=0))
    sid = k.session()
    k.drain()
    assert k.c.get(f"/api/session/{sid}/photo").get_json() == {"status": "failed"}


class Slow(FakeProvider):
    def __init__(self):
        super().__init__(delay_s=0)
        self.release = threading.Event()

    def transform(self, jpeg):
        self.release.wait(5)
        return jpeg


def test_photo_failed_after_timeout(tmp_path, sample_content_dir):
    slow = Slow()
    k = Kiosk(tmp_path, sample_content_dir, provider=slow)
    sid = k.session()
    r = k.c.get(f"/api/session/{sid}/photo")
    assert r.status_code == 202 and r.get_json() == {"status": "pending"}
    k.clock.t += k.cfg.transform_timeout_s + 1
    assert k.c.get(f"/api/session/{sid}/photo").get_json() == {"status": "failed"}
    slow.release.set()


def test_photo_ready_is_jpeg(k):
    sid = k.session()
    k.drain()
    r = k.c.get(f"/api/session/{sid}/photo")
    assert r.status_code == 200 and r.mimetype == "image/jpeg" and r.data[:2] == b"\xff\xd8"


def test_session_uploads_original_and_page(tmp_path, sample_content_dir):
    k = Kiosk(tmp_path, sample_content_dir, provider=Slow())
    sid = k.session()
    tok = k.sess(sid).token
    k.state.cloud.drain()
    page = k.s3.objects[f"p/{tok}"]
    assert page["ContentType"].startswith("text/html") and page["CacheControl"] == "no-cache"
    assert "original.jpg" in page["Body"].decode() and "self-destructs" in page["Body"].decode()
    orig = k.s3.objects[f"tmp/{tok}/original.jpg"]
    assert orig["ContentType"] == "image/jpeg"
    assert orig["ContentDisposition"] == 'attachment; filename="baby-burn-original.jpg"'
    k.state.provider.release.set()


def test_dusty_uploaded_when_transform_done(k):
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    dusty = k.s3.objects[f"tmp/{tok}/dusty.jpg"]
    assert dusty["ContentDisposition"] == 'attachment; filename="baby-burn-dusty.jpg"'
    assert "dusty.jpg" in k.page(tok) and "still developing" not in k.page(tok)
    assert k.state.photos.paths(tok)[1] is not None  # local dusty kept even with an instant transform


def test_choice_album_moves_to_album_prefix(k):
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    r = k.finish(sid)
    k.choose(sid, "album")
    k.drain()
    assert f"album/{SECRET}/{tok}/original.jpg" in k.keys()
    assert f"album/{SECRET}/{tok}/dusty.jpg" in k.keys()
    assert not [x for x in k.keys() if x.startswith(f"tmp/{tok}/")]
    assert "No expiry" in k.page(tok) and "self-destructs in" not in k.page(tok)
    assert r["name"] in k.album_page() and tok in k.album_page()
    assert f"<a href='{BASE}/album/{SECRET}/{tok}/original.jpg'><img src='{BASE}/album/{SECRET}/{tok}/dusty.jpg'" \
        in k.album_page()  # grid shows dusty, tap for original
    assert list((k.cfg.data_dir / "album").glob(f"*{tok}*original.jpg"))


def test_dusty_after_share_goes_to_album(tmp_path, sample_content_dir):
    slow = Slow()
    k = Kiosk(tmp_path, sample_content_dir, provider=slow)
    sid = k.session()
    tok = k.sess(sid).token
    k.finish(sid)
    k.choose(sid, "album")
    k.state.cloud.drain()
    slow.release.set()
    k.drain()
    assert f"album/{SECRET}/{tok}/dusty.jpg" in k.keys()
    assert f"tmp/{tok}/dusty.jpg" not in k.keys()
    assert list((k.cfg.data_dir / "album").glob(f"*{tok}*dusty.jpg"))


def test_choice_delete_removes_everywhere(k):
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    r = k.finish(sid)
    k.choose(sid, "album")
    k.drain()
    k.choose(sid, "delete")
    k.drain()
    assert k.keys(tok) == {f"p/{tok}"}
    assert r["name"] not in k.album_page() and tok not in k.album_page()
    assert "This photo was deleted." in k.page(tok) and ".jpg" not in k.page(tok)
    assert not (k.cfg.data_dir / "photos" / tok).exists()


def test_choice_done_keeps_tmp_and_countdown(k):
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    k.finish(sid)
    k.choose(sid, "done")
    k.drain()
    assert f"tmp/{tok}/original.jpg" in k.keys()
    page = k.page(tok)
    assert "<script" in page and "self-destructs in" in page
    assert (T0 + timedelta(days=14)).isoformat() in page and "Sat Nov 28" in page


def test_upload_failure_does_not_block_finish(tmp_path, sample_content_dir):
    class Down(MemoryS3):
        def put_object(self, **kw):
            raise ConnectionError("no internet")

    k = Kiosk(tmp_path, sample_content_dir, s3=Down())
    sid = k.session()
    r = k.finish(sid)
    assert r["name"] and r["printed"] is True
    k.drain()
    assert k.state.cloud.errors > 0


def test_photo_page_states():
    kw = dict(uploaded_at=T0, album_url=f"{BASE}/album/{SECRET}/index.html")
    temp = render_photo_page("tok", state="temp", has_dusty=True, **kw)
    assert "self-destructs in" in temp and "on or after Sat Nov 28" in temp and "setInterval" in temp
    album = render_photo_page("tok", state="album", has_dusty=True, **kw)
    assert "No expiry" in album and "self-destructs in" not in album
    assert f"album/{SECRET}/tok/dusty.jpg" in album
    gone = render_photo_page("tok", state="deleted", has_dusty=True, **kw)
    assert "This photo was deleted." in gone and "<img" not in gone
    dev = render_photo_page("tok", state="temp", has_dusty=False, **kw)
    assert "still developing" in dev and "3000" in dev
    for page in (temp, album, gone, dev):
        assert not re.search(r"""(src|href)=["']?(https?:)?//(?!ph\.example)""", page)  # no external assets


def test_no_photo_no_upload(k):
    k.finish(k.session(photo=False))
    k.drain()
    assert k.s3.objects == {}


def test_skip_everything(k, monkeypatch):
    calls = []
    real = web.render_badge
    monkeypatch.setattr(web, "render_badge", lambda *a, **kw: calls.append((a, kw)) or real(*a, **kw))
    sid = k.session(photo=False)
    r = k.finish(sid, [])
    assert r["name"] and r["photo_url"] is None and r["first_burn_label"] is None
    (args, kw), = calls
    assert args[2] is None and kw["qr_url"] is None
    assert len(k.prints()) == 1
    assert k.c.get(f"/api/session/{sid}/qr.svg").status_code == 404


def test_badge_has_qr_when_photo(k, monkeypatch):
    calls = []
    real = web.render_badge
    monkeypatch.setattr(web, "render_badge", lambda *a, **kw: calls.append(kw) or real(*a, **kw))
    sid = k.session()
    r = k.finish(sid)
    assert calls[0]["qr_url"] == r["photo_url"]
    qr = k.c.get(f"/api/session/{sid}/qr.svg")
    assert qr.status_code == 200 and qr.mimetype == "image/svg+xml" and b"<svg" in qr.data


def test_tokens_unguessable(k):
    toks = {k.sess(k.session()).token for _ in range(100)}
    assert len(toks) == 100 and all(len(t) >= 22 for t in toks)


def test_abandoned_session_expires(k):
    before = k.state.names.remaining()
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    assert (k.cfg.data_dir / "photos" / tok).is_dir()
    k.clock.t += k.cfg.session_ttl_s + 1
    k.state.sessions.expire()
    r = k.c.get(f"/api/session/{sid}/photo")
    assert r.status_code == 404 and r.get_json() == {"error": "expired"}
    assert not (k.cfg.data_dir / "photos" / tok).exists()
    assert k.state.names.remaining() == before


def test_unknown_session_404(k):
    for method, path in (("post", "/finish"), ("get", "/qr.svg"), ("get", "/photo"), ("post", "/photo-choice")):
        r = getattr(k.c, method)(f"/api/session/nope{path}", json={"choice": "done"})
        assert r.status_code == 404 and r.get_json() == {"error": "expired"}


def test_session_without_photo(k):
    r = k.c.post("/api/session", data={}, content_type="multipart/form-data").get_json()
    assert r["has_photo"] is False and k.sess(r["session_id"]).token is None
    assert k.finish(r["session_id"])["name"]
    assert k.c.get(f"/api/session/{r['session_id']}/photo").get_json() == {"status": "failed"}


def test_rejects_non_jpeg_photo(k):
    r = k.c.post("/api/session", data={"photo": (io.BytesIO(b"<html>"), "x.jpg")}, content_type="multipart/form-data")
    assert r.status_code == 400


def test_only_approved_questions_served(tmp_path, sample_content_dir):
    append_entry(sample_content_dir, "questions", {
        "id": "secret-draft", "type": "single", "prompt": "Draft?", "status": "draft",
        "options": [{"id": "a", "text": "yes", "correct": True}, {"id": "b", "text": "no"}]})
    k = Kiosk(tmp_path, sample_content_dir)
    for _ in range(30):
        assert "secret-draft" not in {q["id"] for q in k.c.get("/api/quiz").get_json()["questions"]}


def test_reload_content_keeps_old_on_error(k, sample_content_dir):
    old = k.state.content
    (sample_content_dir / "names.yaml").write_text("- {id: x")
    k.state.reload_content()
    assert k.state.content is old and k.state.content_error


def test_dev_bucket_when_photo_bucket_unset(tmp_path, sample_content_dir):
    cfg = Config(content_dir=sample_content_dir, data_dir=tmp_path / "data", printer="file")
    app = web.create_app(cfg, provider=FakeProvider(delay_s=0))
    st, c = app.extensions["babyburn"], app.test_client()
    sid = c.post("/api/session", data={"photo": (io.BytesIO(jpeg()), "p.jpg")},
                 content_type="multipart/form-data").get_json()["session_id"]
    url = c.post(f"/api/session/{sid}/finish", json={}).get_json()["photo_url"]
    c.post(f"/api/session/{sid}/photo-choice", json={"choice": "album"})
    st.cloud.drain()
    page = c.get(url)
    assert url.startswith("/cloud/p/") and page.mimetype == "text/html" and b"No expiry" in page.data
    img = re.search(r"src='([^']+original\.jpg)'", page.data.decode()).group(1)
    assert c.get(img).mimetype == "image/jpeg"


def test_real_bucket_needs_base_url_and_secret(tmp_path, sample_content_dir):
    cfg = Config(content_dir=sample_content_dir, data_dir=tmp_path / "data", photo_bucket="b")
    with pytest.raises(ValueError, match="ALBUM_SECRET"):
        web.create_app(cfg, provider=FakeProvider(delay_s=0), s3=MemoryS3())


def test_kiosk_page_served(k):
    assert k.c.get("/kiosk.html").status_code == 200
    assert k.c.get("/kiosk.js").status_code == 200
    assert k.c.get("/").status_code in (200, 302)
