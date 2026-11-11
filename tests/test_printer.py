import pytest
from PIL import Image

from app import printer
from app.printer import PrintError, make_printer


@pytest.fixture
def img():
    return Image.new("1", (954, 672), 1)


def test_file_printer_writes_png(tmp_path, img):
    out = tmp_path / "out"
    make_printer("file", out)(img)
    files = list(out.glob("badge-*.png"))
    assert len(files) == 1
    assert Image.open(files[0]).size == (954, 672)


def test_unknown_target_rejected(tmp_path):
    with pytest.raises(ValueError):
        make_printer("bogus", tmp_path)


@pytest.mark.parametrize("result", [RuntimeError("usb gone"), {"did_print": False, "outcome": "error"}])
def test_usb_failure_raises_printerror(monkeypatch, tmp_path, img, result):
    monkeypatch.setattr(printer, "convert", lambda *a, **k: b"data")

    def fake_send(*a, **k):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(printer, "send", fake_send)
    monkeypatch.setattr(printer, "sleep", lambda s: None)
    with pytest.raises(PrintError):
        make_printer("usb://0x04f9:0x209b", tmp_path)(img)


def test_convert_called_with_60x86_and_rotate(monkeypatch, tmp_path, img):
    calls = {}

    def fake_convert(qlr, images, **kw):
        calls["convert"] = (images, kw)
        return b"data"

    def fake_send(instructions, **kw):
        calls["send"] = (instructions, kw)
        return {"did_print": True}

    monkeypatch.setattr(printer, "convert", fake_convert)
    monkeypatch.setattr(printer, "send", fake_send)
    make_printer("usb://0x04f9:0x209b", tmp_path)(img)
    images, kw = calls["convert"]
    assert images == [img]
    assert kw == dict(label="60x86", rotate="90", threshold=70, dither=False, cut=True)
    assert calls["send"][0] == b"data"
    assert calls["send"][1] == dict(printer_identifier="usb://0x04f9:0x209b", backend_identifier="pyusb")


def test_shift_moves_pixel_and_zero_is_identity():
    im = Image.new("1", (954, 672), 1)
    im.putpixel((100, 50), 0)
    assert printer.shift(im, 0, 0).tobytes() == im.tobytes()
    out = printer.shift(im, -13, -6)
    assert out.size == im.size and out.getpixel((87, 44)) == 0 and out.getpixel((100, 50)) == 1


def test_shift_clips_off_edge():
    im = Image.new("1", (954, 672), 1)
    im.putpixel((2, 2), 0)
    assert printer.shift(im, -5, 0).getextrema()[0] == 1


def test_make_printer_applies_offset(tmp_path):
    im = Image.new("1", (954, 672), 1)
    im.putpixel((100, 50), 0)
    make_printer("file", tmp_path, offset=(-13, -6))(im)
    out = Image.open(next(tmp_path.glob("badge-*.png"))).convert("1")
    assert out.getpixel((87, 44)) == 0


@pytest.mark.parametrize("flag", ["--offset-x", "--offset-y"])
@pytest.mark.parametrize("val", ["61", "-61"])
def test_test_print_rejects_out_of_range_offset(flag, val, capsys):
    from tools import test_print
    with pytest.raises(SystemExit):
        test_print.main(["x", "file", flag, val])
    assert "between -60 and 60" in capsys.readouterr().err


def test_test_print_defaults_from_env(monkeypatch, capsys):
    from tools import test_print
    monkeypatch.setenv("BADGE_OFFSET_X", "-6")
    monkeypatch.setenv("BADGE_OFFSET_Y", "-13")
    monkeypatch.setattr(test_print, "make_printer", lambda t, d, off: lambda img: None)
    test_print.main(["x", "file"])
    assert "offset x=-6 y=-13" in capsys.readouterr().out
    test_print.main(["x", "file", "--offset-x", "3"])
    assert "offset x=3 y=-13" in capsys.readouterr().out


def test_test_print_rejects_unknown_border(capsys):
    from tools import test_print
    with pytest.raises(SystemExit):
        test_print.main(["x", "file", "--border", "bogus"])


def test_test_print_passes_chosen_border(monkeypatch, capsys):
    from tools import test_print
    seen = {}
    monkeypatch.setattr(test_print, "render_badge", lambda *a, **k: seen.update(k) or Image.new("1", (954, 672), 1))
    monkeypatch.setattr(test_print, "make_printer", lambda t, d, off: lambda img: None)
    test_print.main(["x", "file", "--border", "ticket_notches"])
    assert seen["border"] == "ticket_notches"
    assert "border=ticket_notches" in capsys.readouterr().out


def test_test_print_coloring_page_with_offsets(monkeypatch, capsys, tmp_path):
    from tools import test_print
    monkeypatch.setenv("COLORING_DIR", str(tmp_path))
    Image.new("L", (40, 20), 0).save(tmp_path / "page.png")
    printed = []
    monkeypatch.setattr(test_print, "render_badge", lambda *a, **k: pytest.fail("badge rendered"))
    monkeypatch.setattr(test_print, "make_printer", lambda t, d, off: lambda img: printed.append((off, img)))
    test_print.main(["x", "file", "--coloring", "--offset-x", "4"])
    ((off, img),) = printed
    assert off[0] == 4 and img.size == (954, 672) and img.mode == "1"
    assert "coloring page" in capsys.readouterr().out


def test_test_print_coloring_empty_pool_errors(monkeypatch, tmp_path):
    from tools import test_print
    monkeypatch.setenv("COLORING_DIR", str(tmp_path))
    monkeypatch.setattr(test_print, "make_printer", lambda t, d, off: lambda img: pytest.fail("printed"))
    with pytest.raises(SystemExit, match="no readable coloring page"):
        test_print.main(["x", "file", "--coloring"])


@pytest.fixture
def usb(monkeypatch, tmp_path):
    """A USB printer whose send() plays back `outcomes` (an exception or a result dict); waits are recorded."""
    waits, calls, outcomes = [], [], []
    monkeypatch.setattr(printer, "convert", lambda *a, **k: b"data")
    monkeypatch.setattr(printer, "sleep", lambda s: waits.append((s, printer._lock.locked())))

    def fake_send(*a, **k):
        calls.append(printer._lock.locked())
        r = outcomes.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(printer, "send", fake_send)
    return make_printer("usb://0x04f9:0x209b", tmp_path), outcomes, calls, waits


def test_usb_retry_succeeds_on_second_attempt(usb, img):
    p, outcomes, calls, waits = usb
    outcomes += [ValueError("Device not found"), {"did_print": True}]
    p(img)
    assert len(calls) == 2 and waits == [(2, True)]


def test_usb_gives_up_after_three_attempts_waiting_2_then_4_under_the_lock(usb, img):
    p, outcomes, calls, waits = usb
    outcomes += [RuntimeError("usb gone"), {"did_print": False, "outcome": "error"}, ValueError("Device not found")]
    with pytest.raises(PrintError, match="after 3 attempts"):
        p(img)
    assert calls == [True, True, True] and waits == [(2, True), (4, True)]
    assert not printer._lock.locked()


@pytest.mark.parametrize("outcome", ["sent", "unknown", None])
def test_usb_unconfirmed_print_is_not_retried(usb, img, outcome):
    """Sent but not confirmed may already be on the label: a retry would hand the guest a duplicate."""
    p, outcomes, calls, waits = usb
    outcomes += [{"did_print": False, **({"outcome": outcome} if outcome else {})}]
    with pytest.raises(PrintError, match="did not confirm"):
        p(img)
    assert len(calls) == 1 and waits == []
