import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import cloud_check  # noqa: E402

from app.config import Config  # noqa: E402
from app.photos import MemoryS3  # noqa: E402


class FakeS3(MemoryS3):
    """Records the S3 calls the kiosk itself makes: put, copy (album share), batch delete (delete)."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def put_object(self, **kw):
        self.calls.append(("put", kw))
        super().put_object(**kw)

    def copy_object(self, **kw):
        self.calls.append(("copy", kw))
        super().copy_object(**kw)

    def delete_objects(self, **kw):
        self.calls.append(("delete_objects", kw))
        super().delete_objects(**kw)


CFG = Config(photo_bucket="b", photo_base_url="https://pub-x.r2.dev", s3_endpoint_url="https://a.r2.cloudflarestorage.com")


def test_run_puts_copies_prints_url_waits_then_batch_deletes():
    s3, out, seen = FakeS3(), [], []
    url = cloud_check.run(CFG, s3, wait=lambda: seen.append([c[0] for c in s3.calls]), say=out.append)
    assert [c[0] for c in s3.calls] == ["put", "copy", "delete_objects"]
    put, copy, dele = (c[1] for c in s3.calls)
    assert put["Bucket"] == "b" and put["Key"].startswith("tmp/") and put["ContentType"].startswith("text/html")
    assert copy["CopySource"] == {"Bucket": "b", "Key": put["Key"]} and copy["Key"].startswith("tmp/")
    assert {o["Key"] for o in dele["Delete"]["Objects"]} == {put["Key"], copy["Key"]}
    assert url == f"https://pub-x.r2.dev/{copy['Key']}" and url in out
    assert seen == [["put", "copy"]]  # waited after put+copy, before delete
    assert s3.objects == {}


def test_deletes_even_if_wait_interrupted():
    s3 = FakeS3()

    def boom():
        raise KeyboardInterrupt

    try:
        cloud_check.run(CFG, s3, wait=boom, say=lambda _: None)
    except KeyboardInterrupt:
        pass
    assert [c[0] for c in s3.calls] == ["put", "copy", "delete_objects"] and s3.objects == {}


def test_uses_the_kiosk_s3_client():
    from app.photos import make_s3_client

    assert cloud_check.make_s3_client is make_s3_client
