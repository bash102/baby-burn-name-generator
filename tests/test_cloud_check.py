import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import cloud_check  # noqa: E402

from app.config import Config  # noqa: E402


class FakeS3:
    def __init__(self):
        self.calls = []

    def put_object(self, **kw):
        self.calls.append(("put", kw))

    def delete_object(self, **kw):
        self.calls.append(("delete", kw))


CFG = Config(photo_bucket="b", photo_base_url="https://pub-x.r2.dev", s3_endpoint_url="https://a.r2.cloudflarestorage.com")


def test_run_puts_prints_url_waits_then_deletes():
    s3, out, seen = FakeS3(), [], []
    url = cloud_check.run(CFG, s3, wait=lambda: seen.append([c[0] for c in s3.calls]), say=out.append)
    (op, put), (op2, dele) = s3.calls
    assert (op, op2) == ("put", "delete")
    assert put["Bucket"] == "b" and put["Key"] == dele["Key"] and put["Key"].startswith("tmp/")
    assert put["ContentType"].startswith("text/html")
    assert url == f"https://pub-x.r2.dev/{put['Key']}"
    assert url in out
    assert seen == [["put"]]  # waited after put, before delete


def test_deletes_even_if_wait_interrupted():
    s3 = FakeS3()

    def boom():
        raise KeyboardInterrupt

    try:
        cloud_check.run(CFG, s3, wait=boom, say=lambda _: None)
    except KeyboardInterrupt:
        pass
    assert [c[0] for c in s3.calls] == ["put", "delete"]
