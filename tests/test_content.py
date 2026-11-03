import pytest
import yaml

from app.content import BANNED_WORDS, ContentError, append_entry, load_content, set_status


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


def test_option_tags_must_be_in_vocab(sample_content_dir):
    qs = _read(sample_content_dir, "questions")
    qs[1]["options"][0]["tags"] = ["zzz"]
    _write(sample_content_dir, "questions", qs)
    with pytest.raises(ContentError, match="zzz"):
        load_content(sample_content_dir)


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
