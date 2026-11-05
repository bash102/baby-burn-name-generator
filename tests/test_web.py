import html
import io
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from PIL import Image

import app.web as web
from app.config import Config
from app.content import append_entry, set_status
from app.photos import MemoryS3, render_photo_page
from app.printer import PrintError
from app.transform import FakeProvider

BASE = "https://ph.example"
SECRET = "sekret-album-key-16"
ALBUM_STATUS = "This photo is in the Baby Burn album and stays there."
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
    def __init__(self, tmp_path, content_dir, provider=None, printer=None, s3=None, **cfg):
        cfg.setdefault("coloring_odds", 0)  # tests count prints; coloring tests opt in
        self.cfg = Config(content_dir=content_dir, data_dir=tmp_path / "data", printer="file",
                          photo_bucket="b", photo_base_url=BASE, album_secret=SECRET, **cfg)
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


def test_finish_prints_badge_shifted_by_configured_offset(tmp_path, sample_content_dir, monkeypatch):
    def badge(*a, **kw):
        im = Image.new("1", (954, 672), 1)
        im.putpixel((100, 50), 0)
        return im

    monkeypatch.setattr(web, "render_badge", badge)
    k = Kiosk(tmp_path, sample_content_dir, badge_offset_x=-6, badge_offset_y=13)
    assert k.finish(k.session(photo=False))["printed"] is True
    png = Image.open(k.prints()[0]).convert("1")
    assert png.getpixel((94, 63)) == 0 and png.getpixel((100, 50)) == 255


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
    assert k.state.recent_badges[-1][2] == r["name"]


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
    assert "original.jpg" in page["Body"].decode() and "will be deleted around" in page["Body"].decode()
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
    assert ALBUM_STATUS in k.page(tok) and "will be deleted around" not in k.page(tok)
    assert html.escape(r["name"]) in k.album_page() and tok in k.album_page()
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
    assert "ContentDisposition" not in k.s3.objects[f"album/{SECRET}/{tok}/dusty.jpg"]  # album images open inline
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
    assert html.escape(r["name"]) not in k.album_page() and tok not in k.album_page()
    assert "This photo has been deleted." in k.page(tok) and ".jpg" not in k.page(tok)
    assert not (k.cfg.data_dir / "photos" / tok).exists()
    assert not list((k.cfg.data_dir / "album").glob(f"*-{tok}-*.jpg"))  # explicit delete drops the backups


def test_choice_done_keeps_tmp_and_countdown(k):
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    k.finish(sid)
    k.choose(sid, "done")
    k.drain()
    assert f"tmp/{tok}/original.jpg" in k.keys()
    page = k.page(tok)
    assert "<script" in page and "This photo will be deleted around Sat Nov 28 (in about 14 days)." in page
    assert (T0 + timedelta(days=14)).isoformat() in page


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
    kw = dict(uploaded_at=T0, now=T0, album_url=f"{BASE}/album/{SECRET}/index.html")
    temp = render_photo_page("tok", state="temp", has_dusty=True, **kw)
    assert ("This photo will be deleted around Sat Nov 28 (in about 14 days). Download it before then." in temp
            and "setInterval" in temp)
    assert "Download it before then." in temp.split("<script>")[1]  # live countdown uses the same copy
    assert "This photo has been deleted." in temp.split("<script>")[1]  # _GONE_JS
    album = render_photo_page("tok", state="album", has_dusty=True, **kw)
    assert ALBUM_STATUS in album and "will be deleted" not in album
    assert f"album/{SECRET}/tok/dusty.jpg" in album
    gone = render_photo_page("tok", state="deleted", has_dusty=True, **kw)
    assert "This photo has been deleted." in gone and "<img" not in gone
    dev = render_photo_page("tok", state="temp", has_dusty=False, **kw)
    assert "still developing" in dev and "3000" in dev
    assert "Your dusty baby didn't develop. Your original is below." in dev
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
    assert url.startswith("/cloud/p/") and page.mimetype == "text/html" and ALBUM_STATUS.encode() in page.data
    img = re.search(r"src='([^']+original\.jpg)'", page.data.decode()).group(1)
    assert c.get(img).mimetype == "image/jpeg"


def test_real_bucket_needs_base_url_and_secret(tmp_path, sample_content_dir):
    cfg = Config(content_dir=sample_content_dir, data_dir=tmp_path / "data", photo_bucket="b")
    with pytest.raises(ValueError, match="PHOTO_BASE_URL"):
        web.create_app(cfg, provider=FakeProvider(delay_s=0), s3=MemoryS3())
    cfg = Config(content_dir=sample_content_dir, data_dir=tmp_path / "data", photo_bucket="b", photo_base_url=BASE)
    with pytest.raises(ValueError, match="ALBUM_SECRET"):
        web.create_app(cfg, provider=FakeProvider(delay_s=0), s3=MemoryS3())


def test_kiosk_page_served(k):
    assert k.c.get("/kiosk.html").status_code == 200
    assert k.c.get("/kiosk.js").status_code == 200
    assert k.c.get("/").status_code in (200, 302)


def _draft_first_burn(tmp_path, sample_content_dir):
    set_status(sample_content_dir, "questions", "first_burn", "draft")
    return Kiosk(tmp_path, sample_content_dir)


def test_quiz_without_approved_first_burn(tmp_path, sample_content_dir):
    k = _draft_first_burn(tmp_path, sample_content_dir)
    r = k.c.get("/api/quiz")
    qs = r.get_json()["questions"]
    assert r.status_code == 200 and "first_burn" not in {q["id"] for q in qs}
    assert len(qs) == k.cfg.quiz_random_count


def test_finish_ignores_unserved_questions(tmp_path, sample_content_dir):
    k = _draft_first_burn(tmp_path, sample_content_dir)
    r = k.finish(k.session(photo=False), [{"question_id": "first_burn", "option_ids": ["bb2"]}])
    assert r["first_burn_label"] is None


def test_finish_ignores_draft_random_question_tags(tmp_path, sample_content_dir):
    append_entry(sample_content_dir, "questions", {
        "id": "secret-draft", "type": "single", "prompt": "Draft?", "status": "draft",
        "options": [{"id": "a", "text": "yes", "correct": True, "first_burn": 9}, {"id": "b", "text": "no"}]})
    k = Kiosk(tmp_path, sample_content_dir)
    r = k.finish(k.session(photo=False), [{"question_id": "secret-draft", "option_ids": ["a"]}])
    assert r["first_burn_label"] is None


def test_printer_any_exception_keeps_name_and_caches(tmp_path, sample_content_dir):
    def broken(img):
        raise OSError("usb gone")

    k = Kiosk(tmp_path, sample_content_dir, printer=broken)
    sid = k.session(photo=False)
    r = k.finish(sid)
    assert r["printed"] is False and r["name"]
    assert k.finish(sid) == r


def test_dusty_hook_failure_does_not_fail_transform(k, monkeypatch):
    def boom(token, jpeg):
        raise OSError("disk full")

    monkeypatch.setattr(k.state.photos, "save_dusty", boom)
    sid = k.session()
    k.drain()
    assert k.c.get(f"/api/session/{sid}/photo").mimetype == "image/jpeg"


class FlakyDelete(MemoryS3):
    broken = True

    def delete_objects(self, **kw):
        if self.broken:
            raise ConnectionError("offline")
        super().delete_objects(**kw)


def test_delete_reports_cloud_failure_then_succeeds_on_retry(tmp_path, sample_content_dir):
    s3 = FlakyDelete()
    k = Kiosk(tmp_path, sample_content_dir, s3=s3)
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    r = k.c.post(f"/api/session/{sid}/photo-choice", json={"choice": "delete"})
    assert r.status_code == 502 and r.get_json() == {"ok": False}
    assert not (k.cfg.data_dir / "photos" / tok).exists()  # local copies are gone regardless
    r = k.c.get(f"/api/session/{sid}/photo")  # and the kiosk stops showing it
    assert r.status_code == 404 and r.get_json() == {"error": "deleted"}
    assert any(key.startswith("tmp/") for key in k.keys(tok))
    s3.broken = False
    r = k.c.post(f"/api/session/{sid}/photo-choice", json={"choice": "delete"})
    assert r.status_code == 200 and r.get_json() == {"ok": True}
    assert k.keys(tok) == {f"p/{tok}"}



def test_expiry_keeps_album_backups(k):
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    k.finish(sid)
    k.choose(sid, "album")
    k.drain()
    k.clock.t += k.cfg.session_ttl_s + 1
    k.state.sessions.expire()
    assert not (k.cfg.data_dir / "photos" / tok).exists()
    assert {p.name.split(f"-{tok}-")[1] for p in (k.cfg.data_dir / "album").glob(f"*-{tok}-*.jpg")} \
        == {"original.jpg", "dusty.jpg"}


class SlowFirstAlbumPut(MemoryS3):
    """The first album-page write stalls long enough for every other op to finish; the rest jitter."""
    n = 0

    def put_object(self, **kw):
        if kw["Key"].endswith("index.html"):
            self.n += 1
            time.sleep(0.3 if self.n == 1 else random.random() / 500)
        super().put_object(**kw)


def test_album_page_always_matches_index_under_concurrency(tmp_path, sample_content_dir):
    k = Kiosk(tmp_path, sample_content_dir, s3=SlowFirstAlbumPut())
    cloud, toks = k.state.cloud, [f"tok{i:02d}" for i in range(40)]
    for t in toks:
        cloud.share(t, f"Baby {t}")
    for t in toks[1::3]:
        cloud.delete(t)
    k.drain()
    want = {t for t, _ in cloud.album.entries()}
    assert want == set(toks) - set(toks[1::3])
    assert set(re.findall(rf"/album/{SECRET}/(tok\d+)/dusty\.jpg", k.album_page())) == want


def test_cloud_op_chains_are_pruned(k):
    cloud = k.state.cloud
    for i in range(50):
        cloud.upload(f"t{i}", original=b"x")
        cloud.share(f"t{i}", "n")
    k.drain()
    assert cloud._last == {}


def test_share_reports_truthfully(k):
    cloud = k.state.cloud
    assert cloud.share("a", "n").result() is True
    assert cloud.share("a", "n").result() is True  # already shared: idempotent
    cloud.delete("b")
    assert cloud.share("b", "n").result() is False


def test_album_after_delete_is_409(k):
    sid = k.session()
    k.drain()
    k.finish(sid)
    k.choose(sid, "delete")
    r = k.c.post(f"/api/session/{sid}/photo-choice", json={"choice": "album"})
    assert r.status_code == 409 and r.get_json() == {"ok": False}
    k.choose(sid, "done")  # "done" still fine


def test_album_twice_is_ok(k):
    sid = k.session()
    k.drain()
    k.finish(sid)
    k.choose(sid, "album")
    k.choose(sid, "album")


def test_delete_drops_session_photo(k):
    sid = k.session()
    k.drain()
    k.finish(sid)
    k.choose(sid, "delete")
    r = k.c.get(f"/api/session/{sid}/photo")
    assert r.status_code == 404 and r.get_json() == {"error": "deleted"}
    s = k.sess(sid)
    assert s.photo is None and s.transform_future is None and s.tag_future is None


@pytest.mark.parametrize("body", [
    {"answers": [{"question_id": ["x"], "option_ids": ["a"]}]},  # unhashable id
    {"answers": [{"question_id": True, "option_ids": ["a"]}]},
    {"answers": [{"question_id": "ride", "option_ids": "a"}]},
    {"answers": [{"question_id": "ride", "option_ids": [1]}]},
    {"answers": ["ride"]},
    {"answers": "ride"},
    [1, 2],
])
def test_finish_rejects_malformed_answers(k, body):
    sid = k.session(photo=False)
    r = k.c.post(f"/api/session/{sid}/finish", json=body)
    assert r.status_code == 400 and r.get_json()["error"]
    assert k.sess(sid).result is None and not k.prints()  # nothing cached or printed
    assert k.finish(sid, [{"question_id": 1, "option_ids": ["a"]}])["name"]


def test_server_countdown_uses_now():
    kw = dict(state="temp", has_dusty=True, uploaded_at=T0, album_url=f"{BASE}/album/{SECRET}/index.html")
    page = render_photo_page("tok", now=T0 + timedelta(days=3, hours=5, minutes=10), **kw)
    assert "This photo will be deleted around Sat Nov 28 (in about 10 days). Download it before then." in page
    soon = render_photo_page("tok", now=T0 + timedelta(days=14) - timedelta(minutes=30), **kw)
    assert "<p id=status class=status>This photo is about to be deleted. Download it now if you want it.</p>" in soon


@pytest.mark.parametrize("kw, var", [
    ({"album_secret": "change-me"}, "ALBUM_SECRET"),
    ({"album_secret": "short-secret"}, "ALBUM_SECRET"),
    ({"photo_base_url": "https://photos.example.com"}, "PHOTO_BASE_URL"),
])
def test_real_bucket_refuses_placeholders(tmp_path, sample_content_dir, kw, var):
    cfg = Config(content_dir=sample_content_dir, data_dir=tmp_path / "data", photo_bucket="b",
                 **{"photo_base_url": BASE, "album_secret": SECRET, **kw})
    with pytest.raises(ValueError, match=var):
        web.create_app(cfg, provider=FakeProvider(delay_s=0), s3=MemoryS3())


def test_abandoned_unfinished_session_deletes_cloud_copy(k):
    sid = k.session()
    k.drain()
    tok = k.sess(sid).token
    assert f"tmp/{tok}/original.jpg" in k.keys()
    k.clock.t += k.cfg.session_ttl_s + 1
    k.state.sessions.expire()
    k.drain()
    assert k.keys(tok) == {f"p/{tok}"} and "This photo has been deleted." in k.page(tok)


def test_finished_session_expiry_keeps_cloud_copy(k):
    sid = k.session()
    k.finish(sid)
    k.drain()
    tok = k.sess(sid).token
    k.clock.t += k.cfg.session_ttl_s + 1
    k.state.sessions.expire()
    k.drain()
    assert f"tmp/{tok}/original.jpg" in k.keys()


def test_album_images_inline_tmp_images_download(k):
    sid = k.session()
    k.finish(sid)
    k.drain()
    tok = k.sess(sid).token
    assert "attachment" in k.s3.objects[f"tmp/{tok}/original.jpg"]["ContentDisposition"]
    k.choose(sid, "album")
    k.drain()
    for kind in ("original", "dusty"):
        obj = k.s3.objects[f"album/{SECRET}/{tok}/{kind}.jpg"]
        assert "ContentDisposition" not in obj and obj["ContentType"] == "image/jpeg"


def test_photo_page_has_save_tip():
    kw = dict(uploaded_at=T0, now=T0, album_url=f"{BASE}/album/{SECRET}/index.html")
    for state in ("temp", "album"):
        assert "Tip: press and hold the photo → Save to Photos" in render_photo_page(
            "tok", state=state, has_dusty=True, **kw)


def test_late_dusty_still_uploaded_after_kiosk_gives_up(tmp_path, sample_content_dir):
    slow = Slow()
    k = Kiosk(tmp_path, sample_content_dir, provider=slow)
    sid = k.session()
    tok = k.sess(sid).token
    k.finish(sid)
    k.clock.t += k.cfg.transform_timeout_s + 1
    assert k.c.get(f"/api/session/{sid}/photo").get_json() == {"status": "failed"}
    slow.release.set()
    k.drain()
    assert f"tmp/{tok}/dusty.jpg" in k.keys()


@pytest.fixture(autouse=True)
def _restore_socket_timeout():
    import socket
    yield
    socket.setdefaulttimeout(None)  # web._bind sets a process-wide default


class StubServer:
    def __init__(self, binds, host, port, ssl_context):
        self.host = host
        binds.append((host, port, ssl_context))

    def serve_forever(self):
        pass


def _fake_make_server(binds, fail_host=None):
    def make(host, port, app, threaded, ssl_context=None):
        assert threaded
        if host == fail_host:
            raise SystemExit(1)  # what werkzeug does when bind() fails
        return StubServer(binds, host, port, ssl_context)
    return make


def _run_main(monkeypatch, argv, env, tmp_path, make=None):
    binds = []
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(web, "make_server", make or _fake_make_server(binds))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    web.main(argv)
    return binds


def test_main_tls_uses_configured_port_and_certs(tmp_path, monkeypatch):
    env = _tls_env(tmp_path)
    [(host, port, ctx)] = _run_main(monkeypatch, [], env, tmp_path)
    assert (host, port, ctx) == ("0.0.0.0", 9443, (env["TLS_CERT"], env["TLS_KEY"]))


def test_main_missing_cert_names_path(tmp_path, monkeypatch):
    missing = tmp_path / "nope.pem"
    with pytest.raises(SystemExit, match="nope.pem"):
        _run_main(monkeypatch, [], {"TLS_CERT": str(missing), "TLS_KEY": str(missing)}, tmp_path)


def test_main_dev_ignores_listen_port(tmp_path, monkeypatch):
    [(host, port, ctx)] = _run_main(monkeypatch, ["--dev", "--port", "8123"], {"LISTEN_PORT": "9443"}, tmp_path)
    assert (host, port, ctx) == ("127.0.0.1", 8123, None)


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _tls_env(tmp_path):
    cert, key = tmp_path / "c.pem", tmp_path / "k.pem"
    cert.write_text("x")
    key.write_text("x")
    return {"TLS_CERT": str(cert), "TLS_KEY": str(key), "LISTEN_PORT": "9443"}


def test_main_local_http_off_by_default(tmp_path, monkeypatch):
    assert [b[0] for b in _run_main(monkeypatch, [], _tls_env(tmp_path), tmp_path)] == ["0.0.0.0"]


def test_main_binds_tls_before_local_http(tmp_path, monkeypatch):
    binds = _run_main(monkeypatch, [], {**_tls_env(tmp_path), "LOCAL_HTTP_PORT": "9444"}, tmp_path)
    assert binds[0][:2] == ("0.0.0.0", 9443) and binds[1] == ("127.0.0.1", 9444, None)


@pytest.mark.parametrize("fail_host, bound", [("0.0.0.0", []), ("127.0.0.1", ["0.0.0.0"])])
def test_main_exits_nonzero_when_a_listener_cannot_bind(tmp_path, monkeypatch, caplog, fail_host, bound):
    """A dead iPad port must stop the process, so git-deploy's loopback health check cannot pass."""
    binds = []
    with pytest.raises(SystemExit) as e:
        _run_main(monkeypatch, [], {**_tls_env(tmp_path), "LOCAL_HTTP_PORT": "9444"}, tmp_path,
                  make=_fake_make_server(binds, fail_host))
    assert e.value.code not in (0, None) and fail_host in str(e.value)
    assert [b[0] for b in binds] == bound
    assert f"cannot listen on {fail_host}" in caplog.text


def test_main_exits_on_bad_tls_cert(tmp_path, monkeypatch):
    def make(host, port, app, threaded, ssl_context=None):
        import ssl
        raise ssl.SSLError("PEM lib")
    with pytest.raises(SystemExit, match="0.0.0.0:9443"):
        _run_main(monkeypatch, [], _tls_env(tmp_path), tmp_path, make=make)


def test_local_listener_death_is_logged(caplog):
    class Dying:
        host, port = "127.0.0.1", 9444

        def serve_forever(self):
            raise RuntimeError("boom")
    with pytest.raises(RuntimeError):
        web._serve_local(Dying())
    assert "local HTTP listener on 127.0.0.1:9444 died" in caplog.text


def test_main_serves_local_http_on_loopback_for_real(tmp_path, monkeypatch):
    import urllib.request
    from werkzeug.serving import make_server
    port, binds, real = _free_port(), [], []
    stub = _fake_make_server(binds)

    def make(host, p, app, threaded, ssl_context=None):
        if host != "127.0.0.1":
            return stub(host, p, app, threaded, ssl_context)  # the TLS one is only recorded
        real.append(make_server(host, p, app, threaded=threaded))
        return real[-1]

    try:
        _run_main(monkeypatch, [], {**_tls_env(tmp_path), "LOCAL_HTTP_PORT": str(port)}, tmp_path, make=make)
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/quiz", timeout=5) as r:
            assert r.status == 200
    finally:
        for srv in real:
            srv.shutdown()
            srv.server_close()


def test_stalled_tls_client_does_not_freeze_server(tmp_path, sample_content_dir, monkeypatch):
    """A client that connects and never sends a TLS ClientHello must not block other requests."""
    import socket
    import ssl
    import urllib.request

    pytest.importorskip("cryptography")  # Werkzeug's adhoc cert
    monkeypatch.setattr(web, "SOCKET_TIMEOUT_S", 2)
    app = web.create_app(Config(content_dir=sample_content_dir, data_dir=tmp_path / "data"),
                         provider=FakeProvider(delay_s=0))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    old = socket.getdefaulttimeout()
    ctx = ssl._create_unverified_context()
    url = f"https://127.0.0.1:{port}/kiosk.html"
    idle = srv = None
    try:
        srv = web._bind(app, "127.0.0.1", port, "adhoc")
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        deadline = time.monotonic() + 20
        while True:  # wait until it answers
            try:
                urllib.request.urlopen(url, context=ctx, timeout=5).read()
                break
            except OSError:
                assert time.monotonic() < deadline, "server never came up"
                time.sleep(0.2)
        idle = socket.create_connection(("127.0.0.1", port))  # stalls the TLS handshake
        time.sleep(0.2)
        t0 = time.monotonic()
        body = urllib.request.urlopen(url, context=ctx, timeout=8).read()
        assert b"<" in body and time.monotonic() - t0 < 8
    finally:
        socket.setdefaulttimeout(old)
        if idle:
            idle.close()
        if srv:
            srv.shutdown()
            srv.server_close()


def test_quiz_payload_has_retention_days(tmp_path, sample_content_dir):
    from app.photos import RETENTION_DAYS
    k = Kiosk(tmp_path, sample_content_dir)
    assert k.c.get("/api/quiz").get_json()["retention_days"] == RETENTION_DAYS


def test_setup_r2_parses():
    import pathlib, subprocess
    root = pathlib.Path(__file__).resolve().parent.parent
    subprocess.run(["bash", "-n", str(root / "deploy/setup-r2.sh")], check=True)


@pytest.mark.parametrize("left, text", [
    (timedelta(hours=2), "(in about 2 hours)"),
    (timedelta(hours=36), "(in about 36 hours)"),
    (timedelta(hours=60), "(in about 2 days)"),
    (timedelta(hours=1, minutes=40), "(in about 1 hour)"),
])
def test_server_countdown_floors(left, text):
    from app.photos import RETENTION_DAYS
    kw = dict(state="temp", has_dusty=True, uploaded_at=T0, album_url=f"{BASE}/album/{SECRET}/index.html")
    page = render_photo_page("tok", now=T0 + timedelta(days=RETENTION_DAYS) - left, **kw)
    assert text in page and "Download it before then." in page


def test_js_countdown_floors_like_server():
    script = render_photo_page("tok", state="temp", has_dusty=True, uploaded_at=T0,
                               album_url=f"{BASE}/album/{SECRET}/index.html", now=T0).split("<script>")[1]
    assert "Math.floor(ms / 864e5)" in script and "Math.floor(ms / 36e5)" in script and "Math.round" not in script


def test_finish_payload_has_retention_days(k):
    from app.photos import RETENTION_DAYS
    assert k.finish(k.session())["retention_days"] == RETENTION_DAYS


class Recorder:
    """Printer that keeps every label; call n (1-based) in fail_on raises like a jam."""
    def __init__(self, fail_on=()):
        self.calls, self.fail_on, self.labels = 0, set(fail_on), []

    def __call__(self, img):
        self.calls += 1
        if self.calls in self.fail_on:
            raise PrintError("jam")
        self.labels.append(img)


def coloring_pool(content_dir, data=None):
    (content_dir / "coloring").mkdir()
    if data is None:
        Image.new("L", (40, 20), 0).save(content_dir / "coloring" / "page.png")
    else:
        (content_dir / "coloring" / "page.png").write_bytes(data)


def test_coloring_odds_one_prints_badge_then_coloring_page(tmp_path, sample_content_dir):
    coloring_pool(sample_content_dir)
    p = Recorder()
    k = Kiosk(tmp_path, sample_content_dir, printer=p, coloring_odds=1)
    for _ in range(3):
        sid = k.session(photo=False)
        r = k.finish(sid)
        assert r["printed"] is True and r["coloring"] is True
        assert k.finish(sid) == r  # the kiosk's retry is deduped: no second coloring page
    assert len(p.labels) == 6
    for page in p.labels[1::2]:  # the pool page, fitted onto the label
        assert page.size == (954, 672) and page.mode == "1" and page.convert("L").getextrema() == (0, 255)


def test_coloring_odds_zero_never_prints_one(tmp_path, sample_content_dir):
    coloring_pool(sample_content_dir)
    p = Recorder()
    k = Kiosk(tmp_path, sample_content_dir, printer=p, coloring_odds=0)
    assert all(k.finish(k.session(photo=False))["coloring"] is False for _ in range(20))
    assert p.calls == 20


def test_coloring_roughly_one_in_odds(tmp_path, sample_content_dir):
    coloring_pool(sample_content_dir)
    p = Recorder()
    k = Kiosk(tmp_path, sample_content_dir, printer=p, coloring_odds=4)
    k.state.rng = random.Random(1)
    hits = sum(k.finish(k.session(photo=False))["coloring"] for _ in range(80))
    assert 10 <= hits <= 30 and p.calls == 80 + hits


@pytest.mark.parametrize("pool", [None, b"not a png"])  # no pool dir; an unreadable file
def test_no_bonus_without_a_readable_page(tmp_path, sample_content_dir, pool):
    if pool:
        coloring_pool(sample_content_dir, pool)
    p = Recorder()
    k = Kiosk(tmp_path, sample_content_dir, printer=p, coloring_odds=1)
    r = k.finish(k.session(photo=False))
    assert r["printed"] is True and r["coloring"] is False and r["name"] and p.calls == 1


def test_no_coloring_page_when_badge_did_not_print(tmp_path, sample_content_dir):
    coloring_pool(sample_content_dir)
    p = Recorder(fail_on={1})
    k = Kiosk(tmp_path, sample_content_dir, printer=p, coloring_odds=1)
    r = k.finish(k.session(photo=False))
    assert r["printed"] is False and r["coloring"] is False and p.calls == 1


def test_coloring_print_failure_keeps_name(tmp_path, sample_content_dir):
    coloring_pool(sample_content_dir)
    p = Recorder(fail_on={2})
    k = Kiosk(tmp_path, sample_content_dir, printer=p, coloring_odds=1)
    sid = k.session(photo=False)
    r = k.finish(sid)
    assert r["printed"] is True and r["coloring"] is False and r["name"]
    assert k.state.recent_badges[-1][2] == r["name"]
    assert k.finish(sid) == r and p.calls == 2


def test_kiosk_shows_coloring_note():
    js = (Path(web.__file__).parent / "static" / "kiosk.js").read_text()
    assert 'coloring: "Bonus! A coloring page is printing too."' in js
    assert "r.coloring ? " in js and 'location.search.includes("coloring")' in js
