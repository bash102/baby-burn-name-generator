"""Write one preview PNG per sample name: python tools/preview_badges.py out/"""
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.badge import render_badge  # noqa: E402


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    names = yaml.safe_load((ROOT / "content.sample" / "names.yaml").read_text())
    taglines = yaml.safe_load((ROOT / "content.sample" / "taglines.yaml").read_text())
    rng = random.Random(7)
    for i, n in enumerate(names):
        qr = f"https://photos.example.com/p/{n['id']}-abcdefghijklmnop" if i % 5 == 0 else None
        img = render_badge(n["name"], rng.choice(taglines)["text"], rng.choice([None, 1, 2, 3]),
                           qr_url=qr, rng=rng)
        img.save(out / f"{n['id']}.png")
    print(f"wrote {len(names)} badges to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "out"))
