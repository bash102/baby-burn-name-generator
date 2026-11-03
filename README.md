# Baby Burn Name Generator

A Raspberry Pi kiosk (Flask, Python 3.11) for Baby Burn parties. Guests take a
quick quiz and get a never-repeating playa name printed on a Brother QL-800
badge, plus an AI "dusty baby" photo hosted on Cloudflare R2.

## Start here

1. [docs/ops.md](docs/ops.md): setup (Cloudflare, Pi, iPad, printer) and the party-night guide
2. [docs/STATUS.md](docs/STATUS.md): what is done and what is next

## Run locally

Needs [`uv`](https://docs.astral.sh/uv/) (`brew install uv`).

```
uv run --python 3.11 python -m app.web --dev
```

Open http://127.0.0.1:8000/kiosk.html. Add `?stub` to the URL for a no-server demo.

## Develop

```
uv run --python 3.11 --extra dev pytest -q
```

The app runs from the checkout, not as an installed package. Settings come
from environment variables (see `.env.example` for every variable). The app
does not read `.env` itself:

- Mac: `uv run --env-file .env ...`
- Pi: the systemd unit's `EnvironmentFile=`

With nothing set it uses the fake transform provider, file printer, and
`content.sample/`.

## Content

`content.sample/` is tame placeholder content (names, questions, taglines).
Real content lives in the PRIVATE repo `bash102/baby-burn-content` (branch
`drafts-v1` until reviewed). Clone it into `content/` (git-ignored) with
`gh repo clone bash102/baby-burn-content content`, then rsync it to the Pi
(see docs/ops.md). `CONTENT_DIR` overrides the location.

## History

- Design spec: `docs/superpowers/specs/`
- Implementation plan: `docs/superpowers/plans/`
