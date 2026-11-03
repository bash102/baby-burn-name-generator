"""Photo transform + tagging providers (OpenAI, Gemini, fake)."""
import base64
import io
import json
import re
import time
from typing import Collection, Protocol

from PIL import Image

from app.config import Config

DUSTY_PROMPT = (
    "Turn the person in this photo into a playful, adorable dusty BABY version of themselves: "
    "a cute baby Burning Man burner, covered in a light layer of playa dust, wearing goggles and "
    "the same costume, hat and accessories they are wearing in the photo. Keep their face clearly "
    "recognizable (same hair colour, skin tone, expression and features) with soft chubby baby "
    "proportions. Warm golden sunset desert light, dreamy and joyful. Cute and kind, never creepy "
    "or scary. Family friendly: no drugs, nothing sexual. Keep the same framing as the photo."
)

_TAG_PROMPT = (
    "Which of these tags describe the person's look or costume in the photo? Tags: {vocab}. "
    "Reply with only a JSON array of the matching tags, e.g. [\"tag\"]. Use [] if none match."
)


class Provider(Protocol):
    def transform(self, jpeg: bytes) -> bytes: ...
    def tag(self, jpeg: bytes, vocab: Collection[str]) -> set[str]: ...


def parse_tags(text: str, vocab: Collection[str]) -> set[str]:
    """Accept a JSON array or comma list; keep only vocab members. Never raises."""
    try:
        text = text.strip()
        try:
            data = json.loads(text)
            items = data if isinstance(data, list) else []
        except ValueError:
            items = re.split(r"[,\n]", text)
        allowed = {v.lower() for v in vocab}
        return {s for s in (str(i).strip().strip("\"'[]").lower() for i in items) if s in allowed}
    except Exception:
        return set()


def _to_jpeg(data: bytes, max_side: int = 1280, quality: int = 85) -> bytes:
    """Re-encode whatever the model returned as a JPEG well under ~1 MB."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class FakeProvider:
    def __init__(self, delay_s: float = 3.0):
        self.delay_s = delay_s

    def transform(self, jpeg: bytes) -> bytes:
        time.sleep(self.delay_s)
        return jpeg

    def tag(self, jpeg: bytes, vocab: Collection[str]) -> set[str]:
        return set()


class OpenAIProvider:
    def __init__(self, cfg: Config, client=None):
        self.cfg = cfg
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=cfg.openai_api_key, timeout=cfg.transform_timeout_s)
        self.client = client

    def transform(self, jpeg: bytes) -> bytes:
        res = self.client.images.edit(
            model=self.cfg.openai_image_model,
            image=("photo.jpg", jpeg, "image/jpeg"),
            prompt=DUSTY_PROMPT,
        )
        return _to_jpeg(base64.b64decode(res.data[0].b64_json))

    def tag(self, jpeg: bytes, vocab: Collection[str]) -> set[str]:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        res = self.client.responses.create(
            model=self.cfg.openai_vision_model,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": _TAG_PROMPT.format(vocab=sorted(vocab))},
                {"type": "input_image", "image_url": url},
            ]}],
        )
        return parse_tags(res.output_text, vocab)


class GeminiProvider:
    def __init__(self, cfg: Config, client=None):
        self.cfg = cfg
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=cfg.gemini_api_key,
                http_options=types.HttpOptions(timeout=int(cfg.transform_timeout_s * 1000)),
            )
        self.client = client

    @staticmethod
    def _part(jpeg: bytes):
        from google.genai import types

        return types.Part.from_bytes(data=jpeg, mime_type="image/jpeg")

    def transform(self, jpeg: bytes) -> bytes:
        res = self.client.models.generate_content(
            model=self.cfg.gemini_image_model, contents=[DUSTY_PROMPT, self._part(jpeg)]
        )
        for cand in res.candidates or []:
            for part in cand.content.parts or []:
                if part.inline_data and part.inline_data.data:
                    return _to_jpeg(part.inline_data.data)
        raise RuntimeError("Gemini returned no image")

    def tag(self, jpeg: bytes, vocab: Collection[str]) -> set[str]:
        res = self.client.models.generate_content(
            model=self.cfg.gemini_vision_model,
            contents=[_TAG_PROMPT.format(vocab=sorted(vocab)), self._part(jpeg)],
        )
        return parse_tags(res.text, vocab)


def get_provider(name: str, cfg: Config) -> Provider:
    if name == "openai":
        return OpenAIProvider(cfg)
    if name == "gemini":
        return GeminiProvider(cfg)
    if name == "fake":
        return FakeProvider()
    raise ValueError(f"unknown transform provider: {name!r}")
