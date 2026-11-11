"""Look/vibe words: descriptors.yaml, the photo JSON reply, scoring in NameStore.pick, tuning view, admin edits."""
import csv
import io
import json
import random
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest
import yaml

import app.transform as T
from app.config import Config
from app.content import ContentError, append_entry, load_content, update_entry
from app.names import NameStore
from test_admin import AUTH, PIN, post
from test_web import Kiosk, jpeg

LOOKS = {"pink", "feathers", "goggles", "sequins"}


# --- descriptors.yaml and words on names/options ---

def _names(d, edit):
    rows = yaml.safe_load((d / "names.yaml").read_text())
    edit(next(r for r in rows if r["id"] == "dusty-bottom"))
    (d / "names.yaml").write_text(yaml.safe_dump(rows))


def test_sample_descriptors_load(sample_content_dir):
    c = load_content(sample_content_dir)
    assert {"goggles", "fur"} <= c.looks and {"sleepy", "cozy"} <= c.vibes and not c.looks & c.vibes
    assert next(n for n in c.names if n.id == "goggle-eyes").words == {"goggles"}
    sunrise = next(q for q in c.questions if q.id == "sunrise")
    assert next(o for o in sunrise.options if o.id == "c").words == {"sleepy"}


def test_words_are_normalized(sample_content_dir):
    _names(sample_content_dir, lambda r: r.update(words=[" Goggles", "goggles", "SLEEPY"]))
    c = load_content(sample_content_dir)
    assert next(n for n in c.names if n.id == "dusty-bottom").words == {"goggles", "sleepy"}


def test_unknown_word_is_named(sample_content_dir):
    _names(sample_content_dir, lambda r: r.update(words=["goggles", "tutu"]))
    with pytest.raises(ContentError, match=r"names\[dusty-bottom\].*tutu"):
        load_content(sample_content_dir)


def test_too_many_words(sample_content_dir):
    (sample_content_dir / "descriptors.yaml").write_text(yaml.safe_dump(
        {"looks": [f"look{i}" for i in range(9)], "vibes": []}))
    _names(sample_content_dir, lambda r: r.update(words=[f"look{i}" for i in range(9)]))
    with pytest.raises(ContentError, match="at most 8 words"):
        load_content(sample_content_dir)


@pytest.mark.parametrize("desc, msg", [
    ({"looks": ["pink"], "vibes": ["Pink"]}, "more than once"),
    ({"looks": ["very pink hat"], "vibes": []}, "1-2 words"),
    ({"looks": "pink"}, "list of strings"),
])
def test_bad_descriptors(sample_content_dir, desc, msg):
    (sample_content_dir / "descriptors.yaml").write_text(yaml.safe_dump(desc))
    with pytest.raises(ContentError, match=msg):
        load_content(sample_content_dir)


def test_missing_descriptors_is_old_behavior(sample_content_dir):
    for f in ("names.yaml", "questions.yaml"):  # strip every words: key, then drop the file
        rows = yaml.safe_load((sample_content_dir / f).read_text())
        for r in rows:
            r.pop("words", None)
            for o in r.get("options", []):
                o.pop("words", None)
        (sample_content_dir / f).write_text(yaml.safe_dump(rows))
    (sample_content_dir / "descriptors.yaml").unlink()
    c = load_content(sample_content_dir)
    assert c.looks == c.vibes == frozenset() and not any(n.words for n in c.names)
    _names(sample_content_dir, lambda r: r.update(words=["goggles"]))  # words need the file
    with pytest.raises(ContentError, match="goggles"):
        load_content(sample_content_dir)


def test_update_entry_words(sample_content_dir):
    c = update_entry(sample_content_dir, "names", "dusty-bottom", {"words": ["Goggles", "sleepy"]})
    assert next(n for n in c.names if n.id == "dusty-bottom").words == {"goggles", "sleepy"}
    c = update_entry(sample_content_dir, "questions", "ride", {"options": [
        {"id": "a", "text": "Bike", "tags": ["sparkle"], "words": ["show-off"]}, {"id": "b", "text": "Wagon", "tags": ["gnome"]}]})
    ride = next(q for q in c.questions if q.id == "ride")
    assert [o.words for o in ride.options] == [{"show-off"}, frozenset()]
    before = (sample_content_dir / "names.yaml").read_bytes()
    with pytest.raises(ContentError, match="tutu"):
        update_entry(sample_content_dir, "names", "dusty-bottom", {"words": ["tutu"]})
    assert (sample_content_dir / "names.yaml").read_bytes() == before


# --- the photo call ---

def test_prompt_forbids_body_descriptors():
    p = T.photo_prompt({"gnome"}, {"pink"})
    for phrase in ("clothing, costume, colors, materials, accessories, lights and props",
                   "Never describe", "face, body, hair, skin, age, gender or ethnicity", '"looks"', "pink", "gnome"):
        assert phrase in p


@pytest.mark.parametrize("text, want", [
    ('{"tags": ["gnome", "nope"], "looks": ["Pink", "feathers", "tutu"]}', ({"gnome"}, {"pink", "feathers"})),
    ('```json\n{"tags": [], "looks": ["goggles"]}\n```', (set(), {"goggles"})),
    ('["gnome"]', ({"gnome"}, set())),  # the old array reply
    ("gnome, pink", ({"gnome"}, set())),
    ('{"tags": "gnome", "looks": [1, null, "blonde", "skinny"]}', (set(), set())),  # body words are not in looks
    ("lol", (set(), set())), (None, (set(), set())), ("{", (set(), set())),
])
def test_parse_photo(text, want):
    assert T.parse_photo(text, {"gnome", "dust"}, LOOKS) == want


def test_parse_photo_caps_looks():
    looks = {f"l{i}" for i in range(12)}
    assert len(T.parse_photo(json.dumps({"looks": sorted(looks)}), set(), looks)[1]) == 8


def test_providers_send_prompt_and_parse_json():
    seen = []

    class Responses:
        def create(self, **kw):
            seen.append(kw["input"][0]["content"][0]["text"])
            return SimpleNamespace(output_text='{"tags": ["gnome"], "looks": ["pink"]}')

    class Models:
        def generate_content(self, model, contents):
            seen.append(contents[0])
            return SimpleNamespace(text='{"tags": ["gnome"], "looks": ["pink"]}')

    for p in (T.OpenAIProvider(Config(), client=SimpleNamespace(responses=Responses())),
              T.GeminiProvider(Config(), client=SimpleNamespace(models=Models()))):
        assert p.tag(jpeg(), {"gnome"}, LOOKS) == ({"gnome"}, {"pink"})
    assert seen == [T.photo_prompt({"gnome"}, LOOKS)] * 2


def test_fake_provider_looks_are_deterministic():
    assert T.FakeProvider(0).tag(b"x", {"gnome"}, LOOKS) == (set(), set())
    assert T.FakeProvider(0, looks={"pink", "glow"}).tag(b"x", {"gnome"}, LOOKS) == (set(), {"pink"})


# --- scoring ---

@dataclass(frozen=True)
class N:
    id: str
    name: str
    tags: frozenset = frozenset()
    words: frozenset = frozenset()


def store(tmp_path, *pool):
    s = NameStore(tmp_path / "n.sqlite3")
    s.set_pool(pool)
    return s


def picks(s, n=1, **kw):
    kw.setdefault("tags", set())
    return [s.pick(kw.pop("tags"), random.Random(i), **kw)[0] for i in range(n)]


def test_photo_beats_answer_cap(tmp_path):
    s = store(tmp_path, N("photo", "P", words=frozenset({"pink"})),
              N("answers", "A", words=frozenset({"sleepy", "cozy", "chaotic"})))  # 3 > capped 2
    assert picks(s, looks={"pink"}, words={"sleepy", "cozy", "chaotic"}, tags={"gnome"}) == ["photo"]


def test_answer_cap(tmp_path):
    s = store(tmp_path, N("two", "2", tags=frozenset({"gnome"}), words=frozenset({"sleepy", "cozy"})),
              N("three", "3", words=frozenset({"sleepy", "cozy", "chaotic"})))
    explain = {}
    s.pick({"gnome"}, random.Random(0), words={"sleepy", "cozy", "chaotic"}, explain=explain)
    assert explain["score"] == 3 and explain["name"] == "2" and explain["alts"] == [("3", 2)]


def test_tag_cap(tmp_path):
    s = store(tmp_path, N("tags", "T", tags=frozenset({"gnome", "dust", "night"})),
              N("word", "W", tags=frozenset({"gnome"}), words=frozenset({"cozy"})))
    assert picks(s, tags={"gnome", "dust", "night"}, words={"cozy"}) == ["word"]


def test_ties_random_among_top_unused_only(tmp_path):
    s = store(tmp_path, *(N(f"p{i}", f"P{i}", words=frozenset({"pink"})) for i in range(3)), N("x", "X"))
    got = [s.pick(set(), random.Random(i), looks={"pink"})[0] for i in range(3)]
    assert sorted(got) == ["p0", "p1", "p2"]
    assert s.pick(set(), random.Random(9), looks={"pink"})[0] == "x"  # top ones used: fall back to any unused


def test_no_match_falls_back_to_any_unused(tmp_path):
    got = set()
    for seed in range(20):
        s = NameStore(tmp_path / f"s{seed}.sqlite3")
        s.set_pool([N("a", "A", words=frozenset({"pink"})), N("b", "B")])
        got.add(s.pick(set(), random.Random(seed), looks={"sequins"})[0])
    assert got == {"a", "b"}


def test_no_photo_answers_and_tags_drive(tmp_path):
    s = store(tmp_path, N("w", "W", words=frozenset({"sleepy"})), N("t", "T", tags=frozenset({"gnome"})), N("x", "X"))
    explain = {}
    assert s.pick({"gnome"}, random.Random(0), words={"sleepy"}, explain=explain)[0] in {"w", "t"}
    assert explain["score"] == 1


def test_fallback_stranger_explained(tmp_path):
    s = store(tmp_path)
    explain = {}
    assert s.pick(set(), random.Random(0), explain=explain) == ("stranger-1", "Dusty Stranger #1")
    assert explain == {"name": "Dusty Stranger #1", "score": 0, "alts": []}


# --- kiosk + admin ---

def _kiosk(tmp_path, d, **kw):
    k = Kiosk(tmp_path, d, **kw)
    k.state.cfg = replace(k.state.cfg, admin_pin=PIN)
    return k


def test_photo_look_steers_name_and_tuning_is_memory_only(tmp_path, sample_content_dir):
    k = _kiosk(tmp_path, sample_content_dir, provider=T.FakeProvider(0, looks={"goggles"}))
    sid = k.session()
    k.drain()
    r = k.finish(sid, [{"question_id": "sunrise", "option_ids": ["c"]}])
    assert r["name"] == "Goggle Eyes"
    t = k.state.tuning[-1]
    assert t["looks"] == ["goggles"] and t["words"] == ["sleepy"] and t["tags"] == ["beast"]
    assert t["name"] == "Goggle Eyes" and t["score"] == 3 and len(t["alts"]) == 3 and t["alts"][0] == ("Sleepy Sasquatch", 2)
    for _ in range(11):
        k.finish(k.session(photo=False))
    assert len(k.state.tuning) == 10
    assert len(_kiosk(tmp_path, sample_content_dir).state.tuning) == 0  # a restart clears it


def test_dashboard_shows_tuning(tmp_path, sample_content_dir):
    k = _kiosk(tmp_path, sample_content_dir, provider=T.FakeProvider(0, looks={"goggles"}))
    sid = k.session()
    k.drain()
    k.finish(sid)
    html = k.c.get("/admin", headers=AUTH).data.decode()
    assert "Tuning" in html and "goggles" in html and "Goggle Eyes" in html


def test_admin_edit_and_add_words(tmp_path, sample_content_dir):
    k = _kiosk(tmp_path, sample_content_dir)
    r = post(k, "/admin/edit", json={"kind": "names", "id": "dusty-bottom", "fields": {"words": ["Pink", "cozy"]}})
    assert r.status_code == 200 and r.get_json()["row"]["words"] == ["cozy", "pink"]
    r = post(k, "/admin/edit", json={"kind": "names", "id": "dusty-bottom", "fields": {"words": ["tutu"]}})
    assert r.status_code == 400 and "tutu" in r.get_json()["error"]
    post(k, "/admin/add", data={"kind": "names", "name": "Pink Tot", "tags": "dust", "words": "pink, Cozy"})
    assert next(n for n in k.state.content.names if n.name == "Pink Tot").words == {"pink", "cozy"}
    post(k, "/admin/add", data={"kind": "questions", "prompt": "Color?", "opt_a": "Pink", "tags_a": "sparkle",
                                "words_a": "pink", "opt_b": "Grey", "tags_b": "dust"})
    q = next(q for q in k.state.content.questions if q.prompt == "Color?")
    assert [o.words for o in q.options] == [{"pink"}, frozenset()]
    html = k.c.get("/admin/review?kind=names&status=approved", headers=AUTH).data.decode()
    assert 'class="tag w">pink<' in html


# --- persistent match_log + /admin/matching ---

LOG_COLS = ["ts", "photo_state", "looks", "answer_words", "tags", "name_id", "display", "score",
            "photo_pts", "answer_pts", "tag_pts", "decided_by", "top3"]


def test_match_log_row_per_finish(tmp_path, sample_content_dir):
    k = _kiosk(tmp_path, sample_content_dir, provider=T.FakeProvider(0, looks={"goggles"}))
    sid = k.session()
    k.drain()
    k.finish(sid, [{"question_id": "sunrise", "option_ids": ["c"]}])
    k.finish(k.session(photo=False), [{"question_id": "ride", "option_ids": ["b"]}])
    k.finish(k.session(photo=False))
    rows = k.state.names.match_rows()
    assert list(rows[0]) == LOG_COLS  # no photo, token or URL columns
    r = rows[0]
    assert (r["photo_state"], json.loads(r["looks"]), json.loads(r["answer_words"]), json.loads(r["tags"])) == (
        "ready", ["goggles"], ["sleepy"], ["beast"])
    assert (r["name_id"], r["display"], r["score"], r["photo_pts"], r["answer_pts"], r["tag_pts"], r["decided_by"]) == (
        "goggle-eyes", "Goggle Eyes", 3, 3, 0, 0, "photo")
    top3 = json.loads(r["top3"])
    assert len(top3) == 3 and top3[0] == {"name_id": "sleepy-sasquatch", "score": 2}
    assert [(r["photo_state"], r["decided_by"], r["tag_pts"]) for r in rows[1:]] == [
        ("none", "tags", 1), ("none", "random", 0)]
    assert sid not in str(rows) and k.sess(sid).token not in str(rows)


def test_match_log_photo_not_ready_and_stranger(tmp_path):
    s = store(tmp_path)
    s.pick(set(), random.Random(0), photo_state="not_ready")
    (r,) = s.match_rows()
    assert (r["photo_state"], r["decided_by"], r["name_id"], r["top3"]) == (
        "not_ready", "fallback_stranger", "stranger-1", "[]")


def test_match_log_failure_never_fails_finish(tmp_path, sample_content_dir):
    k = _kiosk(tmp_path, sample_content_dir)
    k.state.names._db.execute("DROP TABLE match_log")
    before = k.state.names.remaining()
    assert k.finish(k.session(photo=False))["name"]
    assert k.state.names.remaining() == before - 1


def test_match_log_survives_restart(tmp_path):
    s = store(tmp_path, N("a", "A"))
    s.pick(set(), random.Random(0))
    assert len(NameStore(tmp_path / "n.sqlite3").match_rows()) == 1


def _row(looks=(), words=(), decided="photo", state="ready", score=3):
    return {"ts": "2026-11-14T20:00:00+00:00", "photo_state": state, "looks": json.dumps(list(looks)),
            "answer_words": json.dumps(list(words)), "tags": "[]", "name_id": "x", "display": "X", "score": score,
            "photo_pts": 0, "answer_pts": 0, "tag_pts": 0, "decided_by": decided, "top3": "[]"}


def test_matching_stats_supply_demand():
    from app.admin import matching_stats
    rows = [_row(["pink", "fur"], ["sleepy"]), _row(["pink"]),
            _row(["pink"], state="not_ready", decided="random", score=0), _row(state="none", decided="answers", score=1)]
    st = matching_stats(rows, {"pink", "fur", "goggles", "hat"}, {"pink": 2, "fur": 5, "hat": 0})
    assert st["total"] == 4 and st["photo_guests"] == 3
    assert ("photo", 2, 50) in st["decided"] and ("none", 1, 25) in st["photo_state"]
    assert dict(st["histogram"]) == {0: 1, 1: 1, 3: 2}
    assert st["looks"][0] == ("pink", 3, 100)
    assert st["answers"] == [("sleepy", 1)]
    sd = {w: (d, s, flag) for w, d, s, flag in st["supply_demand"]}
    assert sd["pink"] == (3, 2, True) and sd["fur"] == (1, 5, False) and sd["hat"] == (0, 0, False)
    assert st["never"] == ["goggles", "hat"]


def test_matching_page_and_csv(tmp_path, sample_content_dir):
    k = _kiosk(tmp_path, sample_content_dir, provider=T.FakeProvider(0, looks={"goggles"}))
    r = k.c.get("/admin/matching", headers=AUTH)
    assert r.status_code == 200 and "No guests yet" in r.data.decode()
    assert k.c.get("/admin/matching").status_code == 401 and k.c.get("/admin/matching.csv").status_code == 401
    assert 'href="/admin/matching"' in k.c.get("/admin", headers=AUTH).data.decode()
    sid = k.session()
    k.drain()
    k.finish(sid)
    html = k.c.get("/admin/matching", headers=AUTH).data.decode()
    assert "Goggle Eyes" in html and "goggles" in html and "feathers" in html  # feathers: never detected
    r = k.c.get("/admin/matching.csv", headers=AUTH)
    assert r.mimetype == "text/csv"
    got = list(csv.reader(io.StringIO(r.data.decode())))
    assert got[0] == LOG_COLS and len(got) == 2 and got[1][5] == "goggle-eyes"
