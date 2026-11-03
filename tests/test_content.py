import pytest
import yaml

from app.content import BANNED_WORDS, ContentError, append_entry, load_content, main, set_status


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
    for field, limit in (("option", 45), ("quip", 60)):
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
    append_entry(sample_content_dir, "names", _name("d1", "Draft Dude", status="draft"))
    set_status(sample_content_dir, "names", "d1", "approved")
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


def test_single_question_has_at_most_one_correct_option(sample_content_dir):
    qs = _read(sample_content_dir, "questions")
    q = next(q for q in qs if q["id"] == "margarita")
    assert q["type"] == "single"
    for o in q["options"]:
        o["correct"] = True
    _write(sample_content_dir, "questions", qs)
    with pytest.raises(ContentError, match="exactly one correct"):
        load_content(sample_content_dir)
