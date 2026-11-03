"""In-memory guest sessions: one per kiosk run, with the photo's background transform and tagging."""
import secrets
import threading
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Callable, Collection


@dataclass(eq=False)
class Session:
    id: str
    token: str | None  # photo token (public URL part); None when no photo
    photo: bytes | None
    created_at: float
    transform_future: Future | None = None
    tag_future: Future | None = None
    result: dict | None = None  # cached finish payload
    finished_at: float | None = None
    name_id: str | None = None
    choice: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class SessionStore:
    """Hooks: `on_photo(session)` runs before the transform starts; `on_dusty(session, jpeg)` runs inside
    the transform task, before its future resolves; `on_expire(session)` runs when a session is dropped."""

    def __init__(self, executor, ttl_s: float, now: Callable[[], float], *,
                 on_photo: Callable[[Session], None] | None = None,
                 on_dusty: Callable[[Session, bytes], None] | None = None,
                 on_expire: Callable[[Session], None] | None = None):
        self.executor, self.ttl_s, self.now = executor, ttl_s, now
        self.on_photo, self.on_dusty, self.on_expire = on_photo, on_dusty, on_expire
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, photo: bytes | None, provider, vocab: Collection[str]) -> Session:
        s = Session(secrets.token_urlsafe(16), secrets.token_urlsafe(16) if photo else None, photo, self.now())
        with self._lock:
            self._sessions[s.id] = s
        if photo:
            if self.on_photo:
                self.on_photo(s)

            def transform():
                jpeg = provider.transform(photo)
                if self.on_dusty:
                    self.on_dusty(s, jpeg)
                return jpeg

            s.transform_future = self.executor.submit(transform)
            s.tag_future = self.executor.submit(provider.tag, photo, vocab)
        return s

    def get(self, id: str) -> Session | None:
        with self._lock:
            return self._sessions.get(id)

    def expire(self) -> None:
        cutoff = self.now() - self.ttl_s
        with self._lock:
            old = [s for s in self._sessions.values() if s.created_at < cutoff]
            for s in old:
                del self._sessions[s.id]
        for s in old:
            s.photo = None
            if self.on_expire:
                self.on_expire(s)
