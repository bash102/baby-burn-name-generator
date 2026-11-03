import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, get_type_hints

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    content_dir: Path = ROOT / "content.sample"
    data_dir: Path = Path("data")
    transform_provider: str = "fake"  # openai | gemini | fake
    openai_api_key: str = ""
    gemini_api_key: str = ""
    openai_image_model: str = "gpt-image-1"
    openai_vision_model: str = "gpt-4.1-mini"
    gemini_image_model: str = "gemini-2.5-flash-image"
    gemini_vision_model: str = "gemini-2.5-flash"
    printer: str = "file"  # usb://0x04f9:0x209b | file
    admin_pin: str = ""
    transform_timeout_s: float = 25
    quiz_random_count: int = 4
    low_names_warning: int = 25
    session_ttl_s: int = 600
    photo_bucket: str = ""
    photo_base_url: str = ""  # public URL prefix of the bucket, no trailing slash
    album_secret: str = ""
    photo_retention_days: int = 14
    s3_endpoint_url: str | None = None  # None = AWS
    s3_region: str = "auto"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "Config":
        kw = {}
        hints = get_type_hints(cls)  # resolves string annotations too
        for name, typ in hints.items():
            raw = env.get(name.upper())
            if raw is None or name == "content_dir":
                continue
            if name == "s3_endpoint_url":
                kw[name] = raw or None
            elif name == "data_dir":
                kw[name] = Path(raw)
            elif typ in (float, int):
                try:
                    kw[name] = typ(raw)
                except ValueError:
                    raise ValueError(f"{name.upper()} must be a number, got {raw!r}") from None
            else:
                kw[name] = raw
        content = env.get("CONTENT_DIR")
        if content and Path(content).is_dir():
            kw["content_dir"] = Path(content)
        return cls(**kw)
