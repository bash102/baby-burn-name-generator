import base64
import io
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

import app.transform as T
from app.config import Config


def jpeg(color=(200, 100, 50), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG")
    return buf.getvalue()


def png(size=(32, 32)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buf, "PNG")
    return buf.getvalue()


def test_parse_tags_filters_vocab():
    assert T.parse_tags('["gnome","banana"]', {"gnome", "wizard"}) == {"gnome"}


def test_parse_tags_comma_list_and_case():
    assert T.parse_tags("Gnome, WIZARD, x", {"gnome", "wizard"}) == {"gnome", "wizard"}


def test_parse_tags_garbage():
    assert T.parse_tags("lol", {"gnome"}) == set()
    assert T.parse_tags(None, {"gnome"}) == set()
    assert T.parse_tags('{"a": 1}', {"gnome"}) == set()


def test_fake_provider_roundtrip():
    p = T.FakeProvider(delay_s=0)
    assert p.transform(b"abc") == b"abc"
    assert p.tag(b"abc", {"x"}) == set()


def test_openai_transform_decodes_b64_to_jpeg():
    seen = {}

    class Images:
        def edit(self, **kw):
            seen.update(kw)
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(png()).decode())])

    p = T.OpenAIProvider(Config(openai_api_key="k"), client=SimpleNamespace(images=Images()))
    out = p.transform(jpeg())
    assert Image.open(io.BytesIO(out)).format == "JPEG"
    assert seen["model"] == Config().openai_image_model and seen["prompt"] == T.DUSTY_PROMPT


def test_openai_tag():
    class Responses:
        def create(self, **kw):
            assert kw["model"] == Config().openai_vision_model
            return SimpleNamespace(output_text='["gnome", "nope"]')

    p = T.OpenAIProvider(Config(), client=SimpleNamespace(responses=Responses()))
    assert p.tag(jpeg(), {"gnome"}) == {"gnome"}


def test_gemini_transform_returns_inline_data():
    class Models:
        def generate_content(self, model, contents, **kw):
            assert model == Config().gemini_image_model and contents[0] == T.DUSTY_PROMPT
            part = SimpleNamespace(inline_data=SimpleNamespace(data=png()), text=None)
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])

    p = T.GeminiProvider(Config(), client=SimpleNamespace(models=Models()))
    assert Image.open(io.BytesIO(p.transform(jpeg()))).format == "JPEG"


def test_gemini_no_image_raises():
    class Models:
        def generate_content(self, **kw):
            part = SimpleNamespace(inline_data=None, text="sorry")
            return SimpleNamespace(candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))])

    with pytest.raises(RuntimeError):
        T.GeminiProvider(Config(), client=SimpleNamespace(models=Models())).transform(jpeg())


def test_gemini_tag():
    class Models:
        def generate_content(self, **kw):
            return SimpleNamespace(text="gnome, banana")

    p = T.GeminiProvider(Config(), client=SimpleNamespace(models=Models()))
    assert p.tag(jpeg(), {"gnome"}) == {"gnome"}


def test_get_provider_fake_and_unknown():
    assert isinstance(T.get_provider("fake", Config()), T.FakeProvider)
    with pytest.raises(ValueError):
        T.get_provider("nope", Config())


def test_bakeoff_fake_smoke(tmp_path, monkeypatch):
    photos, out = tmp_path / "p", tmp_path / "o"
    photos.mkdir()
    (photos / "a.jpg").write_bytes(jpeg())
    monkeypatch.setattr(sys, "argv", ["bakeoff", str(photos), str(out), "--providers", "fake"])
    from tools import bakeoff

    bakeoff.main()
    html = (out / "index.html").read_text()
    assert "a.jpg" in html and "fake" in html


def test_gemini_blocked_candidate_raises_clear_error():
    class Models:
        def generate_content(self, **kw):
            return SimpleNamespace(candidates=[SimpleNamespace(content=None, finish_reason="SAFETY")])

    with pytest.raises(RuntimeError, match="Gemini returned no image.*SAFETY"):
        T.GeminiProvider(Config(), client=SimpleNamespace(models=Models())).transform(jpeg())


def test_openai_client_has_no_sdk_retries(monkeypatch):
    import openai

    seen = {}
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: seen.update(kw))
    T.OpenAIProvider(Config(openai_api_key="k"))
    assert seen["max_retries"] == 0 and seen["timeout"] == Config().transform_timeout_s


def test_gemini_client_has_no_sdk_retries(monkeypatch):
    from google import genai

    seen = {}
    monkeypatch.setattr(genai, "Client", lambda **kw: seen.update(kw))
    T.GeminiProvider(Config(gemini_api_key="k"))
    opts = seen["http_options"]
    assert opts.retry_options.attempts == 1
    assert opts.timeout == int(Config().transform_timeout_s * 1000)


def test_bakeoff_missing_key_skips_provider(tmp_path, monkeypatch, capsys):
    photos, out = tmp_path / "p", tmp_path / "o"
    photos.mkdir()
    (photos / "a b.jpg").write_bytes(jpeg())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(sys, "argv", ["bakeoff", str(photos), str(out), "--providers", "openai", "fake"])
    from tools import bakeoff

    bakeoff.main()
    assert "OPENAI_API_KEY" in capsys.readouterr().out
    html = (out / "index.html").read_text()
    assert "orig_a%20b.jpg" in html and "openai" not in html
