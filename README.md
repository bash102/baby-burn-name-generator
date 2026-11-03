# Baby Burn Name Generator

A Raspberry Pi kiosk (Flask, Python 3.11) for Baby Burn parties. Guests take a
quick quiz and get a never-repeating playa name printed on a Brother QL-800
badge, plus an AI "dusty baby" photo hosted on Cloudflare R2.

## Develop

```
uv run --python 3.11 --extra dev pytest -q
```

Copy `.env.example` to `.env` and adjust. With no `.env` the app uses the fake
transform provider, file printer, and `content.sample/`.

## Docs

- Design spec: `docs/superpowers/specs/`
- Implementation plan: `docs/superpowers/plans/`

## Content

`content.sample/` is tame placeholder content (names, questions, taglines).
Real content lives in a separate private repo, cloned into `content/`
(git-ignored). Set `CONTENT_DIR` to point elsewhere.
