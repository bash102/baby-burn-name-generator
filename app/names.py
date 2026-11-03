"""Never-repeat name store: SQLite remembers used ids, the approved pool lives in memory."""
import random
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence


class NameStore:
    def __init__(self, db_path: Path):
        db_path = Path(db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS used ("
            "name_id TEXT PRIMARY KEY, display TEXT NOT NULL, given_at TEXT NOT NULL)"
        )
        self._db.commit()
        self._lock = threading.Lock()
        self._pool: list = []

    def set_pool(self, names: Sequence) -> None:
        with self._lock:
            self._pool = list(names)

    def _unused(self) -> list:
        used = {row[0] for row in self._db.execute("SELECT name_id FROM used")}
        return [n for n in self._pool if n.id not in used]

    def pick(self, guest_tags: set[str], rng: random.Random) -> tuple[str, str]:
        with self._lock, self._db:  # one transaction
            unused = self._unused()
            matching = [n for n in unused if n.tags & guest_tags]
            choice = rng.choice(matching or unused) if unused else None
            if choice:
                name_id, display = choice.id, choice.name
            else:
                n = self._db.execute(
                    "SELECT COUNT(*) FROM used WHERE name_id LIKE 'stranger-%'"
                ).fetchone()[0] + 1
                name_id, display = f"stranger-{n}", f"Dusty Stranger #{n}"
            self._db.execute(
                "INSERT INTO used(name_id, display, given_at) VALUES (?,?,?)",
                (name_id, display, datetime.now(timezone.utc).isoformat()),
            )
            return name_id, display

    def remaining(self) -> int:
        with self._lock:
            return len(self._unused())

    def remaining_by_tag(self) -> dict[str, int]:
        with self._lock:
            return dict(Counter(t for n in self._unused() for t in n.tags))
