import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import album_reset  # noqa: E402

from app.config import Config  # noqa: E402
from app.photos import AlbumIndex, MemoryS3  # noqa: E402


class PagedS3(MemoryS3):
    """Lists two keys per page, to exercise pagination."""

    def list_objects_v2(self, *, Bucket, Prefix="", ContinuationToken=None, **kw):
        keys = [c["Key"] for c in super().list_objects_v2(Bucket=Bucket, Prefix=Prefix)["Contents"]]
        i = int(ContinuationToken or 0)
        page = {"Contents": [{"Key": k} for k in keys[i:i + 2]], "IsTruncated": i + 2 < len(keys)}
        if page["IsTruncated"]:
            page["NextContinuationToken"] = str(i + 2)
        return page


def setup(tmp_path):
    cfg = Config(data_dir=tmp_path, photo_bucket="b", photo_base_url="https://ph.example", album_secret="s3cr3t")
    s3, album = PagedS3(), AlbumIndex(tmp_path / "album.sqlite3")
    for tok in ("t1", "t2"):
        album.add(tok, f"Name {tok}")
        for k in ("original", "dusty"):
            s3.put_object(Bucket="b", Key=f"album/s3cr3t/{tok}/{k}.jpg", Body=b"x")
        s3.put_object(Bucket="b", Key=f"p/{tok}", Body="Shared to the Baby Burn Album")
    s3.put_object(Bucket="b", Key="album/s3cr3t/index.html", Body="Name t1 Name t2")
    s3.put_object(Bucket="b", Key="tmp/t3/original.jpg", Body=b"x")
    return cfg, s3, album


def test_aborts_unless_yes(tmp_path):
    cfg, s3, album = setup(tmp_path)
    before = dict(s3.objects)
    out = []
    assert album_reset.run(cfg, s3, album, ask=lambda _: "y", say=out.append) == 0
    assert s3.objects == before and len(album.entries()) == 2
    assert "5 objects" in out[0]


def test_yes_empties_album(tmp_path):
    cfg, s3, album = setup(tmp_path)
    assert album_reset.run(cfg, s3, album, ask=lambda _: "yes", say=lambda _: None) == 5
    assert not [k for k in s3.objects if k.startswith("album/") and k != "album/s3cr3t/index.html"]
    assert album.entries() == []
    page = s3.objects["album/s3cr3t/index.html"]
    assert page["ContentType"].startswith("text/html") and b"No photos yet" in page["Body"]
    assert b"This photo has been deleted." in s3.objects["p/t1"]["Body"]
    assert "tmp/t3/original.jpg" in s3.objects  # unshared photos untouched


def test_album_photo_opens_full_size_dusty_with_original_link():
    from app.photos import render_album_page
    page = render_album_page([("tok1", "Glitter <Baby>")], "https://x/album/s")
    assert "<a href='https://x/album/s/tok1/dusty.jpg'><img src='https://x/album/s/tok1/dusty.jpg'" in page
    assert "<a class=orig href='https://x/album/s/tok1/original.jpg'>Original</a>" in page
    assert "this.parentNode.href='https://x/album/s/tok1/original.jpg'" in page  # no dusty: link follows the fallback
    assert "Glitter &lt;Baby&gt;" in page
