"""Photo transform + tagging providers (OpenAI, Gemini, fake)."""
import base64
import io
import json
import re
import time
from typing import Collection, Protocol

from PIL import Image

from app.config import Config
from app.content import MAX_WORDS

DUSTY_PROMPT = (
    "Turn the person in this photo into a playful, adorable dusty BABY version of themselves: "
    "a cute baby Burning Man burner, covered in a light layer of playa dust, wearing goggles and "
    "the same costume, hat and accessories they are wearing in the photo. Keep their face clearly "
    "recognizable (same hair color, skin tone, expression and features) with soft chubby baby "
    "proportions. Warm golden sunset desert light, dreamy and joyful. Cute and kind, never creepy "
    "or scary. Family friendly: no drugs, nothing sexual. Keep the same framing as the photo."
)

_PHOTO_PROMPT = (
    "Describe only what the person in the photo is wearing or carrying: clothing, costume, colors, materials, "
    "accessories, lights and props. Never describe their face, body, hair, skin, age, gender or ethnicity. "
    "Pick the tags that fit from: {vocab}. Pick up to {max_looks} looks that are clearly visible from: {looks}. "
    "Use only words from these lists. Reply with only JSON, e.g. "
    '{{"tags": ["tag"], "looks": ["look"]}}; use empty lists if nothing fits.'
)


def photo_prompt(vocab: Collection[str], looks: Collection[str]) -> str:
    return _PHOTO_PROMPT.format(vocab=sorted(vocab), looks=sorted(looks), max_looks=MAX_WORDS)


class Provider(Protocol):
    def transform(self, jpeg: bytes) -> bytes: ...
    def tag(self, jpeg: bytes, vocab: Collection[str], looks: Collection[str] = ()) -> tuple[set[str], set[str]]: ...


def parse_tags(text: str, vocab: Collection[str]) -> set[str]:
    """Accept a JSON array or comma list; keep only vocab members. Never raises."""
    text = (text or "").strip()  # SDKs return None for empty/blocked replies
    try:
        data = json.loads(text)
        items = data if isinstance(data, list) else []
    except ValueError:
        items = re.split(r"[,\n]", text)
    allowed = {v.lower() for v in vocab}
    return {s for s in (str(i).strip().strip("\"'[]").lower() for i in items) if s in allowed}


def parse_photo(text: str, vocab: Collection[str], looks: Collection[str]) -> tuple[set[str], set[str]]:
    """(tags, looks) from the reply {"tags": [...], "looks": [...]}: only list members survive (so no face or
    body word can get through), at most MAX_WORDS looks. An old array/comma reply still gives tags. Never raises."""
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)  # tolerate ```json fences or chatter around the object
    try:
        data = json.loads(m.group(0)) if m else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return parse_tags(text, vocab), set()

    def keep(key, allowed):
        items, allowed = data.get(key), {a.lower() for a in allowed}
        words = (str(i).strip().lower() for i in items) if isinstance(items, list) else ()
        return list(dict.fromkeys(w for w in words if w in allowed))

    return set(keep("tags", vocab)), set(keep("looks", looks)[:MAX_WORDS])


def _to_jpeg(data: bytes, max_side: int = 1280, quality: int = 85) -> bytes:
    """Re-encode whatever the model returned as a JPEG well under ~1 MB."""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


class FakeProvider:
    def __init__(self, delay_s: float = 3.0, looks: Collection[str] = ()):
        self.delay_s, self.looks = delay_s, set(looks)  # looks: what tag() "sees", for tests and dev

    def transform(self, jpeg: bytes) -> bytes:
        time.sleep(self.delay_s)
        return jpeg

    def tag(self, jpeg: bytes, vocab: Collection[str], looks: Collection[str] = ()) -> tuple[set[str], set[str]]:
        return set(), self.looks & set(looks)


class OpenAIProvider:
    def __init__(self, cfg: Config, client=None):
        self.cfg = cfg
        if client is None:
            from openai import OpenAI

            client = OpenAI(api_key=cfg.openai_api_key, timeout=cfg.provider_timeout_s, max_retries=0)
        self.client = client

    def transform(self, jpeg: bytes) -> bytes:
        res = self.client.images.edit(
            model=self.cfg.openai_image_model,
            image=("photo.jpg", jpeg, "image/jpeg"),
            prompt=DUSTY_PROMPT,
        )
        return _to_jpeg(base64.b64decode(res.data[0].b64_json))

    def tag(self, jpeg: bytes, vocab: Collection[str], looks: Collection[str] = ()) -> tuple[set[str], set[str]]:
        url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
        res = self.client.responses.create(
            model=self.cfg.openai_vision_model,
            input=[{"role": "user", "content": [
                {"type": "input_text", "text": photo_prompt(vocab, looks)},
                {"type": "input_image", "image_url": url},
            ]}],
        )
        return parse_photo(res.output_text, vocab, looks)


class GeminiProvider:
    def __init__(self, cfg: Config, client=None):
        self.cfg = cfg
        if client is None:
            from google import genai
            from google.genai import types

            client = genai.Client(
                api_key=cfg.gemini_api_key,
                http_options=types.HttpOptions(
                    timeout=int(cfg.provider_timeout_s * 1000),
                    retry_options=types.HttpRetryOptions(attempts=1),  # one try; caller owns the budget
                ),
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
            for part in getattr(cand.content, "parts", None) or []:  # content is None when blocked
                if part.inline_data and part.inline_data.data:
                    return _to_jpeg(part.inline_data.data)
        reasons = [str(getattr(c, "finish_reason", None)) for c in res.candidates or []]
        raise RuntimeError(f"Gemini returned no image (finish_reason: {', '.join(reasons) or 'no candidates'})")

    def tag(self, jpeg: bytes, vocab: Collection[str], looks: Collection[str] = ()) -> tuple[set[str], set[str]]:
        res = self.client.models.generate_content(
            model=self.cfg.gemini_vision_model,
            contents=[photo_prompt(vocab, looks), self._part(jpeg)],
        )
        return parse_photo(res.text, vocab, looks)


def get_provider(name: str, cfg: Config) -> Provider:
    if name == "openai":
        return OpenAIProvider(cfg)
    if name == "gemini":
        return GeminiProvider(cfg)
    if name == "fake":
        return FakeProvider()
    raise ValueError(f"unknown transform provider: {name!r}")
