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
