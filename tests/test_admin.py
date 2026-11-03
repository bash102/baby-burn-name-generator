import base64
from dataclasses import replace

import pytest
from PIL import Image

from app.config import SAMPLE_DIR
from app.content import append_entry
from app.printer import PrintError, make_printer
from test_web import Kiosk

PIN = "s3cret"
AUTH = {"Authorization": "Basic " + base64.b64encode(f"anyone:{PIN}".encode()).decode()}
POST = {**AUTH, "Origin": "http://localhost"}


def draft(id_, name="Draft <b>Baby</b>", **kw):
    return {"id": id_, "name": name, "tags": ["dust"], "status": "draft", "needs_owner": False, "note": "", **kw}


@pytest.fixture
def k(tmp_path, sample_content_dir):
    append_entry(sample_content_dir, "names", draft("d1"))
    append_entry(sample_content_dir, "names", draft("d2", "Needy Tot", needs_owner=True, note="check <i>this</i>"))
    append_entry(sample_content_dir, "names", draft("d3", "Fave Tot", note="favorite"))
    k = Kiosk(tmp_path, sample_content_dir)
    k.state.cfg = replace(k.state.cfg, admin_pin=PIN)
    return k


def post(k, url, **kw):
    return k.c.post(url, headers=POST, **kw)


def test_requires_pin(k):
    for url in ("/admin", "/admin/review", "/admin/preview/d1.png"):
        assert k.c.get(url).status_code == 401
        assert k.c.get(url, headers={"Authorization": "Basic " + base64.b64encode(b"x:wrong").decode()}).status_code == 401
        assert k.c.get(url, headers=AUTH).status_code == 200


@pytest.mark.parametrize("pin", ["", "change-me"])
def test_unset_pin_disables_admin(k, pin):
    k.state.cfg = replace(k.state.cfg, admin_pin=pin)
    r = k.c.get("/admin", headers={"Authorization": "Basic " + base64.b64encode(f"x:{pin}".encode()).decode()})
    assert r.status_code == 503 and b"ADMIN_PIN" in r.data


def test_csrf_origin_check(k):
    body = {"kind": "names", "id": "d1", "status": "approved"}
    assert k.c.post("/admin/status", headers=AUTH, json=body).status_code == 403
    assert k.c.post("/admin/status", headers={**AUTH, "Origin": "http://evil.example"}, json=body).status_code == 403
    assert k.c.post("/admin/status", headers={**AUTH, "Referer": "http://localhost/admin/review"}, json=body).status_code == 200


def test_approve_makes_name_pickable(k):
    assert "d1" not in {n.id for n in k.state.names._pool}
    assert post(k, "/admin/status", json={"kind": "names", "id": "d1", "status": "approved"}).status_code == 200
    assert "d1" in {n.id for n in k.state.names._pool}
    assert post(k, "/admin/status", json={"kind": "names", "id": "d1", "status": "rejected"}).status_code == 200
    assert "d1" not in {n.id for n in k.state.names._pool}


def test_add_question_appears_in_quiz(k):
    r = post(k, "/admin/add", data={"kind": "questions", "prompt": "Best snack?", "opt_a": "Dust", "correct_a": "on",
                                    "opt_b": "Glitter", "tags_b": "sparkle", "quip": "Crunchy."})
    assert r.status_code == 302 and "Added" in r.headers["Location"]
    k.state.rng.sample = lambda pop, n: list(pop)[-n:]  # newest questions
    qs = k.c.get("/api/quiz").get_json()["questions"]
    q = next(q for q in qs if q["prompt"] == "Best snack?")
    assert [o["correct"] for o in q["options"]] == [True, False]


def test_add_name_and_tagline(k):
    post(k, "/admin/add", data={"kind": "names", "name": "New Tot", "tags": "Dust, gnome"})
    post(k, "/admin/add", data={"kind": "taglines", "text": "Fresh dust"})
    n = next(n for n in k.state.content.names if n.name == "New Tot")
    assert n.status == "approved" and n.tags == {"dust", "gnome"} and n in k.state.names._pool
    assert any(t.text == "Fresh dust" for t in k.state.content.approved_taglines)


def test_reload_failure_keeps_old_content(k):
    (k.cfg.content_dir / "names.yaml").write_text("- {id: broken")
    r = post(k, "/admin/status", json={"kind": "names", "id": "d1", "status": "approved"})
    assert r.status_code == 400 and r.get_json()["error"]
    assert k.c.get("/api/quiz").status_code == 200
    assert b"Content error" in k.c.get("/admin", headers=AUTH).data


def test_sample_content_is_read_only(k):
    k.state.cfg = replace(k.state.cfg, content_dir=SAMPLE_DIR)
    before = (SAMPLE_DIR / "names.yaml").read_bytes()
    r = post(k, "/admin/status", json={"kind": "names", "id": "lil-gnomesayer", "status": "rejected"})
    assert r.status_code == 400 and "public sample content" in r.get_json()["error"]
    post(k, "/admin/add", data={"kind": "taglines", "text": "Nope"})
    assert (SAMPLE_DIR / "names.yaml").read_bytes() == before
    assert b"Using public sample content" in k.c.get("/admin", headers=AUTH).data


def test_review_page(k):
    html = k.c.get("/admin/review?kind=names&status=draft", headers=AUTH).data.decode()
    assert "Draft &lt;b&gt;Baby&lt;/b&gt;" in html and "<b>Baby</b>" not in html  # escaped
    assert 'loading="lazy" alt="badge preview" src="/admin/preview/d1.png"' in html
    assert "check &lt;i&gt;this&lt;/i&gt;" in html and "row needs" in html
    assert html.index("Fave Tot") < html.index("Needy Tot") < html.index("Draft &lt;b&gt;")  # favorite, then needs_owner
    only = k.c.get("/admin/review?kind=names&status=draft&needs_owner=1", headers=AUTH).data.decode()
    assert "Needy Tot" in only and "Fave Tot" not in only
    qs = k.c.get("/admin/review?kind=questions&status=approved", headers=AUTH).data.decode()
    assert "(correct)" in qs and "Quip:" in qs


def test_preview_png(k):
    r = k.c.get("/admin/preview/d1.png", headers=AUTH)
    assert r.mimetype == "image/png" and r.data.startswith(b"\x89PNG")
    assert k.c.get("/admin/preview/nope.png", headers=AUTH).status_code == 404


def test_dashboard(k):
    k.state.content = replace(k.state.content, warnings=["watch <this>"])
    html = k.c.get("/admin", headers=AUTH).data.decode()
    assert "watch &lt;this&gt;" in html and k.state.cloud.album_url() in html
    assert "Names remaining" in html and "Cloud errors" in html


def test_provider_switch(k):
    assert post(k, "/admin/provider", json={"name": "bogus"}).status_code == 400
    assert post(k, "/admin/provider", json={"name": "fake"}).status_code == 200


def test_reprint(k, tmp_path):
    def jammed(img):
        raise PrintError("jammed")

    k.state.printer = jammed
    k.finish(k.session())  # printing fails but the badge is recorded
    k.finish(k.session())
    first, second = (b[0] for b in k.state.recent_badges)
    assert first != second
    assert f'{{&#34;id&#34;: &#34;{first}&#34;}}' in k.c.get("/admin", headers=AUTH).data.decode()
    assert post(k, "/admin/reprint", json={"id": first}).status_code == 502
    out = tmp_path / "reprints"
    k.state.printer = make_printer("file", out)
    assert post(k, "/admin/reprint", json={"id": first}).status_code == 200
    (png,) = out.glob("*.png")
    assert Image.open(png).size == (954, 672)
    for bad in ({"id": "nope"}, {"index": 0}, {}):
        r = post(k, "/admin/reprint", json=bad)
        assert r.status_code == 404 and r.get_json()["error"]
    assert post(k, "/admin/test-print").status_code == 200 and len(list(out.glob("*.png"))) == 2


def test_print_any_exception_is_json(k):
    def unplugged(img):
        raise OSError("usb gone")

    k.state.printer = unplugged
    r = post(k, "/admin/test-print")
    assert r.status_code == 502 and "usb gone" in r.get_json()["error"]


def test_status_bulk(k, monkeypatch):
    reloads = []
    real = k.state.reload_content
    monkeypatch.setattr(k.state, "reload_content", lambda: reloads.append(1) or real())
    r = post(k, "/admin/status-bulk", json={"kind": "names", "ids": ["d1", "d2"], "status": "approved"})
    assert r.status_code == 200 and r.get_json() == {"ok": True} and len(reloads) == 1
    assert {"d1", "d2"} <= {n.id for n in k.state.names._pool}


def test_status_bulk_bad_id_writes_nothing(k):
    path = k.cfg.content_dir / "names.yaml"
    before = path.read_bytes()
    r = post(k, "/admin/status-bulk", json={"kind": "names", "ids": ["d1", "nope"], "status": "approved"})
    assert r.status_code == 400 and "nope" in r.get_json()["error"]
    assert path.read_bytes() == before
    for bad in ({"kind": "names", "ids": "d1", "status": "approved"},
                {"kind": "names", "ids": [["d1"]], "status": "approved"}):
        assert post(k, "/admin/status-bulk", json=bad).status_code == 400
    assert path.read_bytes() == before


def test_non_dict_json_body_is_400(k):
    for url in ("/admin/status", "/admin/status-bulk", "/admin/provider", "/admin/reprint"):
        r = post(k, url, json=[1, 2])
        assert r.status_code == 400 and r.get_json()["error"], url


def test_admin_trailing_slash_redirects(k):
    r = k.c.get("/admin/", headers=AUTH)
    assert r.status_code in (301, 302, 308) and r.headers["Location"].endswith("/admin")


def test_review_copy(k):
    html = k.c.get("/admin/review?kind=names&status=draft", headers=AUTH).data.decode()
    assert "needs your review</label>" in html and "Needs your review: check" in html
    assert "needs owner" not in html.lower()
    assert "(resets to the .env value on restart)" in k.c.get("/admin", headers=AUTH).data.decode()
