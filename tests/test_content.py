import pytest
import yaml

from app.content import (BANNED_WORDS, MAX_OPTION, MAX_QUIP, ContentError, append_entry, load_content, main, set_status,
                         update_entry)


def _read(d, kind):
    return yaml.safe_load((d / f"{kind}.yaml").read_text(encoding="utf-8"))


def _write(d, kind, data):
    (d / f"{kind}.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _name(id, name="Fresh Name", status="approved", tags=("dust",)):
    return {"id": id, "name": name, "tags": list(tags), "status": status, "needs_owner": False, "note": ""}


def test_loads_sample(sample_content_dir):
    c = load_content(sample_content_dir)
    assert len(c.names) == 20
    assert c.first_burn_question.id == "first_burn"
    assert c.first_burn_question not in c.approved_questions
    assert c.warnings == []
    assert {"dust", "gnome"} <= c.tag_vocab
    assert len(c.approved_taglines) == 5


def test_only_approved_served(sample_content_dir):
    append_entry(sample_content_dir, "names", _name("d1", "Draft Dude", status="draft"))
    c = load_content(sample_content_dir)
    assert "d1" not in {n.id for n in c.approved_names}
    assert "d1" in {n.id for n in c.names}


def test_duplicate_ids_rejected(sample_content_dir):
    names = _read(sample_content_dir, "names")
    names.append(_name(names[0]["id"], "Other Name"))
    _write(sample_content_dir, "names", names)
    with pytest.raises(ContentError, match="duplicate id"):
        load_content(sample_content_dir)


def test_duplicate_name_text_case_insensitive_rejected(sample_content_dir):
    names = _read(sample_content_dir, "names")
    names.append(_name("zz", names[0]["name"].upper()))
    _write(sample_content_dir, "names", names)
    with pytest.raises(ContentError, match="duplicate name"):
        load_content(sample_content_dir)


def test_question_needs_a_correct_option(sample_content_dir):
    qs = _read(sample_content_dir, "questions")
    q = next(q for q in qs if q["id"] == "margarita")
    q["options"][0].pop("correct")
    _write(sample_content_dir, "questions", qs)
    with pytest.raises(ContentError, match="correct"):
        load_content(sample_content_dir)


def test_first_burn_exempt_from_correct(sample_content_dir):
    # sample first_burn has no correct option and loads fine
    assert load_content(sample_content_dir).first_burn_question.fixed == "first_burn"


def test_option_tag_not_in_vocab_is_a_warning(sample_content_dir, capsys):
    qs = _read(sample_content_dir, "questions")
    qs[1]["options"][0]["tags"] = ["zzz"]
    _write(sample_content_dir, "questions", qs)
    c = load_content(sample_content_dir)
    assert any("zzz" in w for w in c.warnings)
    # editing must not fail on it either
    set_status(sample_content_dir, "names", "dusty-bottom", "draft")
    append_entry(sample_content_dir, "names", _name("w1", "Warn Me"))
    assert main(["x", str(sample_content_dir)]) == 0
    assert "zzz" in capsys.readouterr().err


@pytest.mark.parametrize("kind,limit,mk", [
    ("names", 40, lambda t: _name("cap", t)),
    ("taglines", 32, lambda t: {"id": "cap", "text": t, "status": "draft"}),
])
def test_text_length_caps(sample_content_dir, kind, limit, mk):
    append_entry(sample_content_dir, kind, mk("x" * limit))
    with pytest.raises(ContentError, match="at most"):
        append_entry(sample_content_dir, kind, {**mk("y" * (limit + 1)), "id": "cap2"})


def test_option_and_quip_length_caps(sample_content_dir):
    orig = _read(sample_content_dir, "questions")
    for field, limit in (("option", MAX_OPTION), ("quip", MAX_QUIP)):
        qs = _read(sample_content_dir, "questions")
        q = next(q for q in qs if q["id"] == "margarita")
        for n, ok in ((limit, True), (limit + 1, False)):
            if field == "option":
                q["options"][0]["text"] = "x" * n
            else:
                q["quip"] = "x" * n
            _write(sample_content_dir, "questions", qs)
            if ok:
                load_content(sample_content_dir)
            else:
                with pytest.raises(ContentError, match="at most"):
                    load_content(sample_content_dir)
                _write(sample_content_dir, "questions", orig)


@pytest.mark.parametrize("bad", ["yes", "true", "2.5"])
def test_first_burn_must_be_int_not_bool(sample_content_dir, bad):
    p = sample_content_dir / "questions.yaml"
    p.write_text(p.read_text().replace("first_burn: 2}", f"first_burn: {bad}}}"), encoding="utf-8")
    with pytest.raises(ContentError, match="first_burn must be an integer"):
        load_content(sample_content_dir)


def test_first_burn_question_none_unless_approved(sample_content_dir):
    qs = _read(sample_content_dir, "questions")
    next(q for q in qs if q.get("fixed")) ["status"] = "draft"
    _write(sample_content_dir, "questions", qs)
    assert load_content(sample_content_dir).first_burn_question is None


def test_concurrent_appends_do_not_lose_entries(sample_content_dir):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: append_entry(sample_content_dir, "names", _name(f"c{i}", f"Concurrent {i}")), range(16)))
    assert len(load_content(sample_content_dir).names) == 20 + 16


def test_banned_word_in_approved_rejected(sample_content_dir):
    word = sorted(BANNED_WORDS)[0]
    names = _read(sample_content_dir, "names")
    names.append(_name("bad", f"Captain {word.title()}"))
    _write(sample_content_dir, "names", names)
    with pytest.raises(ContentError, match="banned"):
        load_content(sample_content_dir)
    names[-1]["status"] = "draft"
    _write(sample_content_dir, "names", names)
    load_content(sample_content_dir)


def test_owner_unblocked_words_load(sample_content_dir):
    names = _read(sample_content_dir, "names")
    names += [_name("e1", "Election Eddie"), _name("m1", "Molly Mapmaker")]
    _write(sample_content_dir, "names", names)
    load_content(sample_content_dir)


def test_banned_matches_whole_words_only(sample_content_dir):
    names = _read(sample_content_dir, "names")
    names.append(_name("ok", "Methodical Mo"))
    _write(sample_content_dir, "names", names)
    load_content(sample_content_dir)


def test_rejects_unrenderable_chars(sample_content_dir):
    names = _read(sample_content_dir, "names")
    names.append(_name("em", "Fire Baby \U0001F525", status="draft"))
    _write(sample_content_dir, "names", names)
    with pytest.raises(ContentError, match="unrenderable"):
        load_content(sample_content_dir)


def test_allows_typographic_chars(sample_content_dir):
    names = _read(sample_content_dir, "names")
    names.append(_name("ty", "Lil’ “Dusty” – Café — Niño"))
    _write(sample_content_dir, "names", names)
    load_content(sample_content_dir)


def test_invalid_yaml_raises_contenterror(sample_content_dir):
    (sample_content_dir / "taglines.yaml").write_text("- {id: a\n  : [", encoding="utf-8")
    with pytest.raises(ContentError, match="taglines.yaml"):
        load_content(sample_content_dir)


def test_set_status_and_append_roundtrip(sample_content_dir):
    c = append_entry(sample_content_dir, "names", _name("d1", "Draft Dude", status="draft"))
    assert c == load_content(sample_content_dir)  # the written content, already validated
    assert set_status(sample_content_dir, "names", "d1", "approved") == load_content(sample_content_dir)
    append_entry(sample_content_dir, "names", _name("n2", "Brand New"))
    c = load_content(sample_content_dir)
    assert {"d1", "n2"} <= {n.id for n in c.approved_names}
    assert isinstance(_read(sample_content_dir, "names"), list)


def test_set_status_unknown_id_or_status(sample_content_dir):
    with pytest.raises(ContentError):
        set_status(sample_content_dir, "names", "nope", "approved")
    with pytest.raises(ContentError):
        set_status(sample_content_dir, "names", "dusty-bottom", "bogus")


def test_append_invalid_does_not_write(sample_content_dir):
    before = (sample_content_dir / "names.yaml").read_bytes()
    with pytest.raises(ContentError):
        append_entry(sample_content_dir, "names", _name("dusty-bottom", "Other"))
    assert (sample_content_dir / "names.yaml").read_bytes() == before


def test_unknown_kind_raises_contenterror(sample_content_dir):
    with pytest.raises(ContentError):
        set_status(sample_content_dir, "bogus", "x", "approved")
    with pytest.raises(ContentError):
        append_entry(sample_content_dir, "bogus", {"id": "x"})


def test_atomic_write_preserves_mode(sample_content_dir):
    p = sample_content_dir / "names.yaml"
    p.chmod(0o640)
    set_status(sample_content_dir, "names", "dusty-bottom", "draft")
    assert p.stat().st_mode & 0o777 == 0o640


def test_stranger_prefix_id_rejected(sample_content_dir):
    with pytest.raises(ContentError, match="stranger-"):
        append_entry(sample_content_dir, "names", _name("stranger-1", "Some Stranger"))


def test_tags_are_lowercased_on_load(sample_content_dir):
    names = _read(sample_content_dir, "names")
    names.append(_name("up", "Upper Tot", tags=("Sparkle",)))
    _write(sample_content_dir, "names", names)
    qs = _read(sample_content_dir, "questions")
    q = next(q for q in qs if any(o.get("tags") for o in q["options"]))
    o = next(o for o in q["options"] if o.get("tags"))
    o["tags"] = [t.upper() for t in o["tags"]]
    _write(sample_content_dir, "questions", qs)
    c = load_content(sample_content_dir)
    assert "sparkle" in c.tag_vocab and "Sparkle" not in c.tag_vocab
    opt = next(x for x in next(x for x in c.questions if x.id == q["id"]).options if x.id == o["id"])
    assert opt.tags and all(t == t.lower() for t in opt.tags) and c.warnings == []


def test_single_question_may_have_several_correct_options(sample_content_dir):
    qs = _read(sample_content_dir, "questions")
    q = next(q for q in qs if q["id"] == "margarita")
    assert q["type"] == "single"
    for o in q["options"][:2]:
        o["correct"] = True
    _write(sample_content_dir, "questions", qs)
    loaded = next(x for x in load_content(sample_content_dir).questions if x.id == "margarita")
    assert sum(o.correct for o in loaded.options) == 2


def _get(d, kind, id):
    return next(r for r in _read(d, kind) if r["id"] == id)


def test_update_name(sample_content_dir):
    append_entry(sample_content_dir, "names", {**_name("u1", "Old Tot", status="draft"), "needs_owner": True,
                                               "note": "check"})
    c = update_entry(sample_content_dir, "names", "u1", {"name": " New Tot ", "tags": ["Dust", " gnome ", "", "DUST", "dust"]})
    assert c == load_content(sample_content_dir)
    assert _get(sample_content_dir, "names", "u1") == {"id": "u1", "name": "New Tot", "tags": ["dust", "gnome"],
                                                       "status": "draft", "needs_owner": True, "note": "check"}


def test_update_tagline(sample_content_dir):
    c = update_entry(sample_content_dir, "taglines", "tiny-burner", {"text": "Tiny burner, huge dust"})
    assert next(t for t in c.taglines if t.id == "tiny-burner").text == "Tiny burner, huge dust"
    assert _get(sample_content_dir, "taglines", "tiny-burner")["status"] == "approved"


def test_update_question(sample_content_dir):
    update_entry(sample_content_dir, "questions", "margarita", {
        "prompt": "Margarita?", "type": "multi", "quip": "",
        "options": [{"id": "a", "text": "Gatorade", "correct": True},
                    {"text": "Dust", "correct": True, "tags": ["Dust"]}, {"text": "Ice"}]})
    q = _get(sample_content_dir, "questions", "margarita")
    assert q == {"id": "margarita", "type": "multi", "prompt": "Margarita?",
                 "options": [{"id": "a", "text": "Gatorade", "correct": True},
                             {"id": "c", "text": "Dust", "correct": True, "tags": ["dust"]},
                             {"id": "d", "text": "Ice"}],
                 "status": "approved", "needs_owner": False}  # quip removed, other keys kept; removed b not reused


def test_update_new_option_ids_skip_old_ids(sample_content_dir):
    qs = _read(sample_content_dir, "questions")
    q = next(q for q in qs if q["id"] == "margarita")
    q["options"] = [{"id": i, "text": i.upper(), "correct": i == "a"} for i in "abcd"]
    _write(sample_content_dir, "questions", qs)
    c = update_entry(sample_content_dir, "questions", "margarita", {"options": [
        {"id": "a", "text": "A", "correct": True}, {"text": "New 1"}, {"text": "New 2"}]})
    assert [o.id for o in next(q for q in c.questions if q.id == "margarita").options] == ["a", "e", "f"]


def test_update_keeps_more_than_four_options(sample_content_dir):
    qs = _read(sample_content_dir, "questions")
    for q in qs:
        if q["id"] in ("margarita", "first_burn"):
            q["options"] += [{"id": f"x{i}", "text": f"Extra {i}"} for i in range(3)]
    _write(sample_content_dir, "questions", qs)
    for qid in ("margarita", "first_burn"):
        opts = _get(sample_content_dir, "questions", qid)["options"]
        assert len(opts) > 4
        edited = [{"id": o["id"], "text": o["text"] + "!", "correct": bool(o.get("correct")), "tags": o.get("tags", [])}
                  for o in opts]
        update_entry(sample_content_dir, "questions", qid, {"options": edited})
        assert _get(sample_content_dir, "questions", qid)["options"] == [{**o, "text": o["text"] + "!"} for o in opts]
    five = [{"id": o["id"], "text": o["text"]} for o in _get(sample_content_dir, "questions", "margarita")["options"]]
    five[0]["correct"] = True
    with pytest.raises(ContentError, match="2-5"):  # margarita has 5 now: no sixth
        update_entry(sample_content_dir, "questions", "margarita", {"options": five + [{"text": "six"}]})


@pytest.mark.parametrize("kind,id,fields", [
    ("names", "dusty-bottom", {"status": "draft"}),
    ("names", "dusty-bottom", {"id": "other"}),
    ("names", "dusty-bottom", {"note": "hi"}),
    ("names", "dusty-bottom", {"name": 5}),
    ("names", "dusty-bottom", {"tags": "dust"}),
    ("names", "nope", {"name": "X"}),
    ("taglines", "tiny-burner", {"name": "X"}),
    ("questions", "margarita", {"type": "essay"}),
    ("questions", "margarita", {"options": [{"id": "a", "text": "x", "correct": True}]}),
    ("questions", "margarita", {"options": [{"text": str(i), "correct": i == 0} for i in range(5)]}),
    ("questions", "margarita", {"options": [{"id": "a", "text": "x", "correct": True}, {"text": "y", "first_burn": 1}]}),
    ("questions", "margarita", {"options": [{"id": "zz", "text": "x", "correct": True}, {"text": "y"}]}),
    ("questions", "margarita", {"options": [{"id": "a", "text": "x", "correct": "yes"}, {"text": "y"}]}),
    ("questions", "margarita", {"options": [{"id": ["a"], "text": "x", "correct": True}, {"text": "y"}]}),
    ("questions", "margarita", {"options": [{"id": {"a": 1}, "text": "x", "correct": True}, {"text": "y"}]}),
    ("names", "dusty-bottom", {"name": "x" * 41}),
    ("names", "dusty-bottom", {"name": "Captain " + sorted(BANNED_WORDS)[0]}),
    ("names", "dusty-bottom", {"name": "Toadstool Tot"}),  # duplicate name
    ("taglines", "tiny-burner", {"text": "x" * 33}),
    ("questions", "margarita", {"quip": "x" * (MAX_QUIP + 1)}),
])
def test_update_rejected_writes_nothing(sample_content_dir, kind, id, fields):
    before = {k: (sample_content_dir / f"{k}.yaml").read_bytes() for k in ("names", "questions", "taglines")}
    with pytest.raises(ContentError):
        update_entry(sample_content_dir, kind, id, fields)
    assert before == {k: (sample_content_dir / f"{k}.yaml").read_bytes() for k in before}


def test_update_first_burn_text_only(sample_content_dir):
    opts = _get(sample_content_dir, "questions", "first_burn")["options"]
    update_entry(sample_content_dir, "questions", "first_burn", {
        "prompt": "Your first Baby Burn?", "quip": "Welcome home.", "type": "single",
        "options": [{"id": o["id"], "text": o["text"] + "!", "correct": False, "tags": []} for o in opts]})
    q = _get(sample_content_dir, "questions", "first_burn")
    assert q["prompt"] == "Your first Baby Burn?" and q["quip"] == "Welcome home." and q["fixed"] == "first_burn"
    assert q["options"] == [{**o, "text": o["text"] + "!"} for o in opts]


@pytest.mark.parametrize("change", [
    lambda o: {"options": [{"id": x["id"], "text": x["text"]} for x in o[:2]]},  # fewer options
    lambda o: {"options": [{"id": x["id"], "text": x["text"]} for x in o] + [{"text": "BB4"}]},
    lambda o: {"options": [{"id": "z" + x["id"], "text": x["text"]} for x in o]},  # new ids
    lambda o: {"options": [{"id": x["id"], "text": x["text"], "correct": True} for x in o]},
    lambda o: {"options": [{"id": x["id"], "text": x["text"], "tags": ["dust"]} for x in o]},
    lambda o: {"options": [{"id": x["id"], "text": x["text"], "first_burn": 9} for x in o]},
    lambda o: {"type": "multi"},
])
def test_update_first_burn_structure_locked(sample_content_dir, change):
    before = (sample_content_dir / "questions.yaml").read_bytes()
    opts = _get(sample_content_dir, "questions", "first_burn")["options"]
    with pytest.raises(ContentError):
        update_entry(sample_content_dir, "questions", "first_burn", change(opts))
    assert (sample_content_dir / "questions.yaml").read_bytes() == before
