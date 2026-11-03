import yaml


def load(d, name):
    return yaml.safe_load((d / name).read_text())


def test_sample_content_shape(sample_content_dir):
    names = load(sample_content_dir, "names.yaml")
    questions = load(sample_content_dir, "questions.yaml")
    taglines = load(sample_content_dir, "taglines.yaml")
    assert (len(questions), len(taglines)) == (6, 5)
    assert all(x["status"] == "approved" for x in names + questions + taglines)
    assert len({n["id"] for n in names}) == 20
    assert sum("gnome" in n["tags"] for n in names) >= 3

    fixed = [q for q in questions if q.get("fixed") == "first_burn"]
    assert len(fixed) == 1
    assert [o["id"] for o in fixed[0]["options"]] == ["bb1", "bb2", "bb3"]
    assert any(q["type"] == "multi" for q in questions)

    name_tags = {t for n in names for t in n["tags"]}
    option_tags = {t for q in questions for o in q["options"] for t in o.get("tags", [])}
    assert option_tags <= name_tags
    assert any("gnome" in o.get("tags", []) for q in questions if not q.get("fixed") for o in q["options"])
