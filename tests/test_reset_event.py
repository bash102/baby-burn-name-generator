import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import reset_event  # noqa: E402

from test_album_reset import setup  # noqa: E402


def seed(tmp_path):
    cfg, s3, album = setup(tmp_path)  # data_dir is tmp_path
    (tmp_path / "names.sqlite3").write_bytes(b"used names")
    (tmp_path / "photos" / "tok").mkdir(parents=True)
    (tmp_path / "photos" / "tok" / "original.jpg").write_bytes(b"x")
    return cfg, s3, album


def test_aborts_unless_yes(tmp_path):
    cfg, s3, album = seed(tmp_path)
    before = dict(s3.objects)
    assert reset_event.run(cfg, s3, album, ask=lambda _: "no", say=lambda _: None, running=list) is False
    assert s3.objects == before and len(album.entries()) == 2
    assert (tmp_path / "names.sqlite3").exists() and (tmp_path / "photos" / "tok" / "original.jpg").exists()


def test_yes_resets_names_album_and_photos(tmp_path):
    cfg, s3, album = seed(tmp_path)
    asked = []
    assert reset_event.run(cfg, s3, album, ask=lambda q: asked.append(q) or "yes", say=lambda _: None,
                           running=list) is True
    assert len(asked) == 1  # one confirmation for the whole reset
    assert not (tmp_path / "names.sqlite3").exists()
    assert not list((tmp_path / "photos").glob("*"))
    assert album.entries() == []
    assert not [k for k in s3.objects if k.startswith("album/") and k != "album/s3cr3t/index.html"]
    assert b"No photos yet" in s3.objects["album/s3cr3t/index.html"]["Body"]


def test_refuses_while_either_kiosk_unit_is_active(tmp_path):
    cfg, s3, album = seed(tmp_path)
    said = []
    assert reset_event.run(cfg, s3, album, ask=lambda _: "yes", say=said.append,
                           running=lambda: ["app-babyburn"]) is False
    assert (tmp_path / "names.sqlite3").exists() and "sudo systemctl stop app-babyburn" in said[-1]


def test_running_units_asks_systemctl_for_both_unit_names():
    seen = []

    def run(argv, **kw):
        seen.append(argv)
        return reset_event.subprocess.CompletedProcess(argv, 0 if argv[-1] == "babyburn" else 3)
    assert reset_event.running_units(run) == ["babyburn"]
    assert seen == [["systemctl", "is-active", "--quiet", u] for u in ("babyburn", "app-babyburn")]
