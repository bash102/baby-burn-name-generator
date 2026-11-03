"""Smoke-test the photo bucket: upload a test page, print its public URL, wait, delete it.

Usage: python tools/cloud_check.py   (reads .env via the environment; see docs/ops.md)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config  # noqa: E402

PAGE = "<!doctype html><meta name=viewport content='width=device-width'><h1>Baby Burn cloud check OK</h1>"


def make_client(cfg: Config):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=cfg.s3_endpoint_url,
        region_name=cfg.s3_region,
        aws_access_key_id=cfg.s3_access_key_id,
        aws_secret_access_key=cfg.s3_secret_access_key,
    )


def run(cfg: Config, s3, wait=lambda: input("Open the URL on a phone, then press Enter to delete it... "), say=print) -> str:
    key = f"tmp/cloud-check-{int(time.time())}.html"  # tmp/ so the lifecycle rule cleans up leftovers
    s3.put_object(Bucket=cfg.photo_bucket, Key=key, Body=PAGE.encode(), ContentType="text/html; charset=utf-8")
    url = f"{cfg.photo_base_url}/{key}"
    try:
        say(url)
        wait()
    finally:
        s3.delete_object(Bucket=cfg.photo_bucket, Key=key)
        say("deleted test page")
    return url


if __name__ == "__main__":
    cfg = Config.from_env()
    if not (cfg.photo_bucket and cfg.photo_base_url and cfg.s3_access_key_id):
        sys.exit("PHOTO_BUCKET, PHOTO_BASE_URL and S3_* must be set (see docs/ops.md)")
    run(cfg, make_client(cfg))
