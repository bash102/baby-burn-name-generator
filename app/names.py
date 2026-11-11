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

# Photo dominates (owner's call): one photo look match (4) outscores the most answers + tags can add (2 + 1).
PHOTO_WEIGHT, ANSWER_CAP, TAG_CAP = 4, 2, 1

# One row per badge for event-wide tuning. Words and scores only: never the photo, its token/URL or a session id.
MATCH_LOG_COLS = ("ts", "photo_state", "looks", "answer_words", "tags", "name_id", "display", "score",
                  "photo_pts", "answer_pts", "tag_pts", "decided_by", "top3")
# One row per badge handed out (failed prints too), for reprints. ts is UTC ISO; printed is 0/1.
BADGE_COLS = ("id", "ts", "name_id", "display", "tagline", "first_burn", "photo_url", "printed")


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
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS badges (id TEXT PRIMARY KEY, ts TEXT NOT NULL, name_id TEXT NOT NULL, "
            "display TEXT NOT NULL, tagline TEXT NOT NULL, first_burn INTEGER, photo_url TEXT, printed INTEGER NOT NULL)"
        )
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
            _, pts, choice = rng.choice([t for t in scored if t[0] == best]) if scored else (0, (0, 0, 0), None)
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
            # Tuning data only: a logging fault must never cost the guest their name or undo the `used` row
            # (never-repeat), so the log write gets its own savepoint inside the pick's transaction.
            self._db.execute("SAVEPOINT log")
            try:
                self._db.execute(
                    f"INSERT INTO match_log({', '.join(MATCH_LOG_COLS)}) VALUES ({', '.join('?' * len(MATCH_LOG_COLS))})",
                    (now, photo_state, json.dumps(sorted(looks)), json.dumps(sorted(words)), json.dumps(sorted(guest_tags)),
                     name_id, display, sum(pts), *pts, decided,
                     json.dumps([{"name_id": a.id, "score": s} for s, a in alts])))
            except sqlite3.Error:
                log.exception("match_log write failed")
                self._db.execute("ROLLBACK TO log")
            self._db.execute("RELEASE log")
            if explain is not None:
                explain.update(name=display, score=sum(pts), alts=[(a.name, s) for s, a in alts])
            return name_id, display

    def match_rows(self) -> list[dict]:
        """The whole match log, oldest first (JSON columns stay JSON text)."""
        with self._lock:
            cur = self._db.execute(f"SELECT {', '.join(MATCH_LOG_COLS)} FROM match_log ORDER BY rowid")
            return [dict(zip(MATCH_LOG_COLS, r)) for r in cur]

    def add_badge(self, id_: str, name_id: str, display: str, tagline: str, first_burn: int | None,
                  photo_url: str | None, printed: bool) -> None:
        """Remember a badge (printed or not) so the admin can reprint it, across restarts."""
        with self._lock, self._db:
            self._db.execute(f"INSERT INTO badges({', '.join(BADGE_COLS)}) VALUES ({', '.join('?' * len(BADGE_COLS))})",
                             (id_, datetime.now(timezone.utc).isoformat(), name_id, display, tagline, first_burn,
                              photo_url, int(printed)))

    def badges(self, q: str = "") -> list[dict]:
        """Badges whose name contains q (any case), failed prints first, then newest first."""
        # ponytail: full scan filtered in Python (casefold handles non-ASCII); fine for the hundreds a party makes.
        with self._lock:
            rows = self._db.execute(f"SELECT {', '.join(BADGE_COLS)} FROM badges ORDER BY printed, rowid DESC").fetchall()
        q = q.strip().casefold()
        return [dict(zip(BADGE_COLS, r)) for r in rows if q in r[3].casefold()]

    def badge(self, id_: str) -> dict | None:
        with self._lock:
            r = self._db.execute(f"SELECT {', '.join(BADGE_COLS)} FROM badges WHERE id = ?", (id_,)).fetchone()
        return dict(zip(BADGE_COLS, r)) if r else None

    def mark_printed(self, id_: str) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE badges SET printed = 1 WHERE id = ?", (id_,))

    def remaining(self) -> int:
        with self._lock:
            return len(self._unused())

    def remaining_by_tag(self) -> dict[str, int]:
        with self._lock:
            return dict(Counter(t for n in self._unused() for t in n.tags))

    def remaining_by_word(self) -> dict[str, int]:
        with self._lock:
            return dict(Counter(w for n in self._unused() for w in n.words))
