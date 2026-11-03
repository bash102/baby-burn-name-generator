"""Smoke-test the photo bucket with the kiosk's own S3 client and calls: put a test page, copy it (what album
share does), print the copy's public URL, wait, then batch-delete both (what photo delete does).

Usage: uv run --env-file .env python tools/cloud_check.py   (see docs/ops.md)
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Config  # noqa: E402
from app.photos import delete_keys, make_s3_client, put_html  # noqa: E402

PAGE = "<!doctype html><meta name=viewport content='width=device-width'><h1>Baby Burn cloud check OK</h1>"


def run(cfg: Config, s3, wait=lambda: input("Open the URL on a phone, then press Enter to delete it... "), say=print) -> str:
    bucket, stamp = cfg.photo_bucket, int(time.time())
    key, copy = f"tmp/cloud-check-{stamp}.html", f"tmp/cloud-check-{stamp}-copy.html"  # tmp/: lifecycle cleans leftovers
    put_html(s3, bucket, key, PAGE)
    try:
        s3.copy_object(Bucket=bucket, Key=copy, CopySource={"Bucket": bucket, "Key": key})
        url = f"{cfg.photo_base_url}/{copy}"
        say(url)
        wait()
    finally:
        delete_keys(s3, bucket, [key, copy])
        say("deleted test pages")
    return url


if __name__ == "__main__":
    cfg = Config.from_env()
    if not (cfg.photo_bucket and cfg.photo_base_url and cfg.s3_access_key_id):
        sys.exit("PHOTO_BUCKET, PHOTO_BASE_URL and S3_* must be set (see docs/ops.md)")
    run(cfg, make_s3_client(cfg))
