import random
import threading
from dataclasses import dataclass

from app.names import NameStore


@dataclass(frozen=True)
class N:
    id: str
    name: str
    tags: frozenset
    words: frozenset = frozenset()


def mk(prefix, tag, n):
    return [N(f"{prefix}{i}", f"{prefix.title()} {i}", frozenset({tag})) for i in range(n)]


def store(tmp_path, pool):
    s = NameStore(tmp_path / "sub" / "names.sqlite3")
    s.set_pool(pool)
    return s


def test_never_repeats(tmp_path):
    s = store(tmp_path, mk("a", "x", 50))
    rng = random.Random(1)
    ids = [s.pick({"x"}, rng)[0] for _ in range(50)]
    assert len(set(ids)) == 50
    assert s.pick({"x"}, rng) == ("stranger-1", "Dusty Stranger #1")
    assert s.pick(set(), rng) == ("stranger-2", "Dusty Stranger #2")


def test_matching_tag_always_wins(tmp_path):
    s = store(tmp_path, mk("g", "gnome", 3) + mk("d", "dust", 3))
    rng = random.Random(2)
    assert all(s.pick({"gnome"}, rng)[0].startswith("g") for _ in range(3))


def test_falls_back_to_random_when_category_empty(tmp_path):
    s = store(tmp_path, mk("g", "gnome", 3) + mk("d", "dust", 3))
    rng = random.Random(3)
    for _ in range(3):
        s.pick({"gnome"}, rng)
    assert s.pick({"gnome"}, rng)[0].startswith("d")


def test_multiple_tags_pool_together(tmp_path):
    s = store(tmp_path, mk("g", "gnome", 3) + mk("d", "dust", 3) + mk("o", "other", 3))
    rng = random.Random(4)
    ids = [s.pick({"gnome", "dust"}, rng)[0] for _ in range(6)]
    assert {i[0] for i in ids} == {"g", "d"}
    assert s.pick({"gnome", "dust"}, rng)[0].startswith("o")


def test_survives_restart(tmp_path):
    s = store(tmp_path, mk("a", "x", 3))
    first = s.pick({"x"}, random.Random(5))[0]
    s2 = store(tmp_path, mk("a", "x", 3))
    assert s2.remaining() == 2
    assert first not in {s2.pick({"x"}, random.Random(i))[0] for i in range(2)}


def test_pool_swap_keeps_used(tmp_path):
    s = store(tmp_path, mk("a", "x", 3))
    used = {s.pick({"x"}, random.Random(6))[0] for _ in range(3)}
    s.set_pool(mk("a", "x", 3) + mk("b", "x", 2))
    assert s.remaining() == 2
    assert s.pick({"x"}, random.Random(7))[0] not in used
    assert s.remaining_by_tag() == {"x": 1}


def test_concurrent_picks_unique(tmp_path):
    s = store(tmp_path, mk("a", "x", 80))
    out, lock = [], threading.Lock()

    def work(seed):
        rng = random.Random(seed)
        for _ in range(10):
            r = s.pick({"x"}, rng)[0]
            with lock:
                out.append(r)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(out) == 80 and len(set(out)) == 80


def test_set_pool_rejects_stranger_ids(tmp_path):
    import pytest
    s = NameStore(tmp_path / "n.sqlite3")
    with pytest.raises(ValueError, match="stranger-"):
        s.set_pool([N("stranger-1", "X", frozenset())])


def test_badge_log_search_order_and_mark_printed(tmp_path):
    s = NameStore(tmp_path / "n.sqlite3")
    s.add_badge("a1", "n1", "Dusty Ånka", "t1", 3, "https://x/p", True)
    s.add_badge("b2", "n2", "Sandy Bob", "t2", None, None, False)
    s.add_badge("c3", "n3", "dusty Cal", "t3", None, None, True)
    assert [b["id"] for b in s.badges()] == ["b2", "c3", "a1"]  # failed first, then newest first
    assert [b["id"] for b in s.badges("DUSTY")] == ["c3", "a1"]
    assert [b["id"] for b in s.badges("ånka")] == ["a1"]
    b = s.badge("a1")
    assert (b["display"], b["tagline"], b["first_burn"], b["photo_url"], b["printed"]) == ("Dusty Ånka", "t1", 3, "https://x/p", 1)
    assert b["ts"].endswith("+00:00")
    assert s.badge("nope") is None
    s.mark_printed("b2")
    assert s.badge("b2")["printed"] == 1
    s._db.close()
    assert NameStore(tmp_path / "n.sqlite3").badge("b2")["display"] == "Sandy Bob"  # survives a restart
