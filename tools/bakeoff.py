"""Compare transform providers on a folder of photos.

Usage: uv run python -m tools.bakeoff PHOTOS_DIR OUT_DIR [--prompt-file f] [--providers openai gemini]
Writes OUT_DIR/index.html (original | provider columns, latency, cost).
"""
import argparse
import html
import shutil
import time
import urllib.parse
from pathlib import Path

import app.transform as T
from app.config import Config

# Approximate USD per image, checked 2026-11-02. Re-check before relying on them.
# OpenAI: token-priced, $30/1M image-output tokens (~1.3k tokens for a medium 1024px image)
#   plus input; estimate only. https://developers.openai.com/api/docs/pricing
# Gemini gemini-3.1-flash-image, 1K: $0.067. https://aistudio.google.com/docs/pricing
# Keyed by image model id (the app/config.py defaults); a model not listed shows $0.000.
_D = Config()
COST_PER_IMAGE_USD = {_D.openai_image_model: 0.05, _D.gemini_image_model: 0.067, "fake": 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("photos_dir", type=Path)
    ap.add_argument("out_dir", type=Path)
    ap.add_argument("--prompt-file", type=Path)
    ap.add_argument("--providers", nargs="+", default=["openai", "gemini"])
    args = ap.parse_args()

    if args.prompt_file:
        T.DUSTY_PROMPT = args.prompt_file.read_text().strip()  # providers read the module global
    cfg = Config.from_env()
    providers = {}
    for n in args.providers:
        if n != "fake" and not getattr(cfg, f"{n}_api_key", ""):
            print(f"skipping {n}: set {n.upper()}_API_KEY")
            continue
        providers[n] = T.get_provider(n, cfg)
    if "fake" in providers:
        providers["fake"].delay_s = 0
    args.out_dir.mkdir(parents=True, exist_ok=True)

    photos = sorted(p for p in args.photos_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg"})
    rows = []
    for photo in photos:
        shutil.copy(photo, args.out_dir / f"orig_{photo.name}")
        orig_src = html.escape(urllib.parse.quote(f"orig_{photo.name}"))
        cells = [f'<td><img src="{orig_src}"><br>{html.escape(photo.name)}</td>']
        for name, prov in providers.items():
            start = time.monotonic()
            try:
                out = prov.transform(photo.read_bytes())
                secs = time.monotonic() - start
                fname = f"{name}_{photo.stem}.jpg"
                (args.out_dir / fname).write_bytes(out)
                src = html.escape(urllib.parse.quote(fname))
                cells.append(f'<td><img src="{src}"><br>{secs:.1f}s, {len(out) // 1024} KB</td>')
            except Exception as e:  # keep going; record the failure
                secs = time.monotonic() - start
                cells.append(f"<td>ERROR after {secs:.1f}s: {html.escape(repr(e))}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")

    head = "".join(
        f"<th>{html.escape(n)}<br>~${COST_PER_IMAGE_USD.get(getattr(cfg, f'{n}_image_model', n), 0):.3f}/image</th>"
        for n in providers
    )
    (args.out_dir / "index.html").write_text(
        "<!doctype html><meta charset=utf-8><title>Bake-off</title>"
        "<style>img{max-width:320px}td,th{vertical-align:top;padding:6px}</style>"
        f"<table><tr><th>original</th>{head}</tr>{''.join(rows)}</table>"
    )
    print(f"wrote {args.out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
