"""Never-repeat name store: SQLite remembers used ids (and logs each match), the approved pool lives in memory."""
import json
import logging
import random
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from app.content import RESERVED_NAME_PREFIX

log = logging.getLogger(__name__)

# Photo dominates: one photo look match (3) beats the answer-word cap (2) and ties answers + tag (2 + 1).
PHOTO_WEIGHT, ANSWER_CAP, TAG_CAP = 3, 2, 1

# One row per badge for event-wide tuning. Words and scores only: never the photo, its token/URL or a session id.
MATCH_LOG_COLS = ("ts", "photo_state", "looks", "answer_words", "tags", "name_id", "display", "score",
                  "photo_pts", "answer_pts", "tag_pts", "decided_by", "top3")


def points(n, looks, words, tags) -> tuple[int, int, int]:
    """(photo, answer, tag) points of name n."""
    w = n.words
    return PHOTO_WEIGHT * len(looks & w), min(ANSWER_CAP, len(words & w)), min(TAG_CAP, len(tags & n.tags))


class NameStore:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS used ("
            "name_id TEXT PRIMARY KEY, display TEXT NOT NULL, given_at TEXT NOT NULL)"
        )
        self._db.execute(f"CREATE TABLE IF NOT EXISTS match_log ({', '.join(MATCH_LOG_COLS)})")
        self._db.commit()
        self._lock = threading.Lock()
        self._pool: list = []

    def set_pool(self, names: Sequence) -> None:
        names = list(names)
        if bad := [n.id for n in names if n.id.startswith(RESERVED_NAME_PREFIX)]:
            raise ValueError(f"pool ids may not start with {RESERVED_NAME_PREFIX!r} (reserved for fallbacks): {bad}")
        with self._lock:
            self._pool = names

    def _unused(self) -> list:
        used = {row[0] for row in self._db.execute("SELECT name_id FROM used")}
        return [n for n in self._pool if n.id not in used]

    def pick(self, guest_tags: set[str], rng: random.Random, looks=frozenset(), words=frozenset(),
             photo_state: str = "none", explain: dict | None = None) -> tuple[str, str]:
        """The best-scoring unused name (random among ties; any unused one when nothing scores). `looks` come from
        the photo, `words` from the chosen answers. Fills `explain` with the chosen score and top 3 alternatives."""
        looks, words, guest_tags = set(looks), set(words), set(guest_tags)
        with self._lock, self._db:  # one transaction
            unused = self._unused()
            scored = [(sum(p), p, n) for n in unused for p in [points(n, looks, words, guest_tags)]]
            best = max((s for s, _, _ in scored), default=0)
            choice = rng.choice([n for s, _, n in scored if s == best] if best else unused) if unused else None
            pts = next((p for _, p, n in scored if n is choice), (0, 0, 0))
            alts = sorted(((s, n) for s, _, n in scored if n is not choice), key=lambda x: -x[0])[:3]
            if choice:
                name_id, display = choice.id, choice.name
                decided = ("photo" if pts[0] else "answers" if pts[1] else "tags" if pts[2] else "random")
            else:
                n = self._db.execute(
                    "SELECT COUNT(*) FROM used WHERE name_id LIKE ?", (RESERVED_NAME_PREFIX + "%",)
                ).fetchone()[0] + 1
                name_id, display, decided = f"{RESERVED_NAME_PREFIX}{n}", f"Dusty Stranger #{n}", "fallback_stranger"
            now = datetime.now(timezone.utc).isoformat()
            self._db.execute("INSERT INTO used(name_id, display, given_at) VALUES (?,?,?)", (name_id, display, now))
            try:  # tuning data only: a logging fault must never cost the guest their name
                self._db.execute(
                    f"INSERT INTO match_log({', '.join(MATCH_LOG_COLS)}) VALUES ({', '.join('?' * len(MATCH_LOG_COLS))})",
                    (now, photo_state, json.dumps(sorted(looks)), json.dumps(sorted(words)), json.dumps(sorted(guest_tags)),
                     name_id, display, sum(pts), *pts, decided,
                     json.dumps([{"name_id": a.id, "score": s} for s, a in alts])))
            except sqlite3.Error:
                log.exception("match_log write failed")
            if explain is not None:
                explain.update(name=display, score=sum(pts), alts=[(a.name, s) for s, a in alts])
            return name_id, display

    def match_rows(self) -> list[dict]:
        """The whole match log, oldest first (JSON columns stay JSON text)."""
        with self._lock:
            cur = self._db.execute(f"SELECT {', '.join(MATCH_LOG_COLS)} FROM match_log ORDER BY rowid")
            return [dict(zip(MATCH_LOG_COLS, r)) for r in cur]

    def remaining(self) -> int:
        with self._lock:
            return len(self._unused())

    def remaining_by_tag(self) -> dict[str, int]:
        with self._lock:
            return dict(Counter(t for n in self._unused() for t in n.tags))

    def remaining_by_word(self) -> dict[str, int]:
        with self._lock:
            return dict(Counter(w for n in self._unused() for w in n.words))
