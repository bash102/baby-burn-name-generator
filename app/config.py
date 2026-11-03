import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Mapping

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
        for f in fields(cls):
            raw = env.get(f.name.upper())
            if raw is None or f.name == "content_dir":
                continue
            if f.name == "s3_endpoint_url":
                kw[f.name] = raw or None
            elif f.name == "data_dir":
                kw[f.name] = Path(raw)
            elif f.type in (float, int):
                kw[f.name] = f.type(raw)
            else:
                kw[f.name] = raw
        content = Path(env.get("CONTENT_DIR", ""))
        if env.get("CONTENT_DIR") and content.is_dir():
            kw["content_dir"] = content
        return cls(**kw)
