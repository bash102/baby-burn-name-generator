import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, get_type_hints

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "content.sample"
PROVIDERS = {"openai", "gemini", "fake"}


@dataclass(frozen=True)
class Config:
    content_dir: Path = SAMPLE_DIR
    data_dir: Path = ROOT / "data"
    transform_provider: str = "fake"  # openai | gemini | fake
    openai_api_key: str = ""
    gemini_api_key: str = ""
    openai_image_model: str = "gpt-image-2.5-flare"
    openai_vision_model: str = "gpt-5-mini"
    gemini_image_model: str = "gemini-3.1-flash-image"
    gemini_vision_model: str = "gemini-3.5-flash-lite"
    printer: str = "file"  # usb://0x04f9:0x209b | file
    admin_pin: str = ""
    transform_timeout_s: float = 25  # kiosk display budget: /photo reports failed after this
    provider_timeout_s: float = 90  # AI HTTP timeout; a late dusty image still reaches the phone page
    quiz_random_count: int = 4
    low_names_warning: int = 25
    session_ttl_s: int = 600
    photo_bucket: str = ""
    photo_base_url: str = ""  # trailing slash stripped by from_env
    album_secret: str = ""
    s3_endpoint_url: str | None = None  # None = AWS
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    @property
    def content_is_sample(self) -> bool:
        return self.content_dir.resolve() == SAMPLE_DIR

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        kw = {}
        for name, typ in get_type_hints(cls).items():  # resolves postponed annotations
            var = name.upper()
            raw = env.get(var)
            if raw is None or (raw == "" and typ is Path):
                continue
            if typ is Path:
                kw[name] = ROOT / raw  # absolute raw wins; relative anchors at the repo root
            elif typ in (int, float):
                try:
                    kw[name] = value = typ(raw)
                except ValueError:
                    raise ValueError(f"{var} must be a number, got {raw!r}") from None
                if not (math.isfinite(value) and value > 0):
                    raise ValueError(f"{var} must be a finite number > 0, got {raw!r}")
            elif typ == (str | None):
                kw[name] = raw or None
            else:
                kw[name] = raw

        content = env.get("CONTENT_DIR")
        if content:
            if not kw["content_dir"].is_dir():
                raise ValueError(f"CONTENT_DIR is not an existing directory: {kw['content_dir']}")
        else:
            kw["content_dir"] = ROOT / "content" if (ROOT / "content").is_dir() else SAMPLE_DIR
        if "photo_base_url" in kw:
            kw["photo_base_url"] = kw["photo_base_url"].rstrip("/")
        if kw.get("transform_provider", cls.transform_provider) not in PROVIDERS:
            raise ValueError(
                f"TRANSFORM_PROVIDER must be one of {sorted(PROVIDERS)}, got {kw['transform_provider']!r}"
            )
        return cls(**kw)
