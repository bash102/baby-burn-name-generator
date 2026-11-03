"""Load, validate and edit the party content (names, questions, taglines) in YAML."""

import os
import re
import stat
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

Kind = Literal["names", "questions", "taglines"]
KINDS: tuple[str, ...] = ("names", "questions", "taglines")
STATUSES = frozenset({"draft", "approved", "rejected"})
QUESTION_TYPES = frozenset({"single", "multi"})

# Safety net only (drug and political terms, whole words, case-insensitive). The owner's review is the real gate.
BANNED_WORDS: frozenset[str] = frozenset({
    "cocaine", "heroin", "meth", "methamphetamine", "lsd", "mdma", "ecstasy", "molly", "ketamine",
    "weed", "marijuana", "cannabis", "shrooms", "psilocybin", "opium", "fentanyl", "dmt", "ayahuasca",
    "trump", "biden", "obama", "reagan", "putin", "republican", "republicans", "democrat", "democrats",
    "gop", "maga", "liberal", "liberals", "conservative", "conservatives", "election",
})

RESERVED_NAME_PREFIX = "stranger-"  # fallback ids minted by app.names
_ALLOWED_EXTRA = "’“”–—"
_WORD = re.compile(r"[a-z0-9]+")


class ContentError(Exception):
    """Content failed to load or validate; the message lists every problem."""


@dataclass(frozen=True)
class Name:
    id: str
    name: str
    tags: frozenset[str]
    status: str
    needs_owner: bool
    note: str


@dataclass(frozen=True)
class Option:
    id: str
    text: str
    correct: bool
    tags: frozenset[str]
    first_burn: int | None


@dataclass(frozen=True)
class Question:
    id: str
    type: str
    prompt: str
    options: list[Option]
    quip: str
    status: str
    needs_owner: bool
    fixed: str | None


@dataclass(frozen=True)
class Tagline:
    id: str
    text: str
    status: str


@dataclass(frozen=True)
class Content:
    names: list[Name]
    questions: list[Question]
    taglines: list[Tagline]

    @property
    def approved_names(self) -> list[Name]:
        return [n for n in self.names if n.status == "approved"]

    @property
    def approved_questions(self) -> list[Question]:
        return [q for q in self.questions if q.status == "approved" and not q.fixed]

    @property
    def first_burn_question(self) -> Question:
        return next(q for q in self.questions if q.fixed == "first_burn")

    @property
    def approved_taglines(self) -> list[Tagline]:
        return [t for t in self.taglines if t.status == "approved"]

    @property
    def tag_vocab(self) -> frozenset[str]:
        return frozenset(t for n in self.approved_names for t in n.tags)


def _renderable(ch: str) -> bool:
    return 0x20 <= ord(ch) <= 0x7E or 0xA0 <= ord(ch) <= 0xFF or ch in _ALLOWED_EXTRA


def _check_text(where: str, text: Any, approved: bool, errs: list[str]) -> None:
    if not isinstance(text, str) or not text.strip():
        errs.append(f"{where}: text must be a non-empty string")
        return
    bad = sorted({c for c in text if not _renderable(c)})
    if bad:
        errs.append(f"{where}: unrenderable characters {bad!r} in {text!r}")
    hits = BANNED_WORDS.intersection(_WORD.findall(text.lower()))
    if approved and hits:
        errs.append(f"{where}: banned word(s) {sorted(hits)} in {text!r}")


def _dicts(kind: str, rows: Any, errs: list[str]) -> list[dict]:
    if not isinstance(rows, list):
        errs.append(f"{kind}.yaml: top level must be a list")
        return []
    good = []
    for i, r in enumerate(rows):
        if isinstance(r, dict):
            good.append(r)
        else:
            errs.append(f"{kind}[{i}]: entry must be a mapping")
    return good


def _common(kind: str, r: dict, seen: set, errs: list[str]) -> tuple[str, str, bool]:
    id_ = r.get("id")
    where = f"{kind}[{id_}]"
    if not isinstance(id_, str) or not id_:
        errs.append(f"{kind}: entry missing string id: {r!r}")
    elif id_ in seen:
        errs.append(f"{where}: duplicate id")
    seen.add(id_)
    status = r.get("status")
    if status not in STATUSES:
        errs.append(f"{where}: status must be one of {sorted(STATUSES)}, got {status!r}")
    return where, status, status == "approved"


def _tags(where: str, r: dict, errs: list[str]) -> frozenset[str]:
    tags = r.get("tags") or []
    if not isinstance(tags, list) or not all(isinstance(t, str) and t for t in tags):
        errs.append(f"{where}: tags must be a list of strings")
        return frozenset()
    return frozenset(tags)


def _build(raw: dict[str, Any]) -> Content:
    errs: list[str] = []

    names, seen, texts = [], set(), set()
    for r in _dicts("names", raw["names"], errs):
        where, status, ok = _common("names", r, seen, errs)
        if str(r.get("id")).startswith(RESERVED_NAME_PREFIX):
            errs.append(f"{where}: id may not start with {RESERVED_NAME_PREFIX!r} (reserved for fallback names)")
        _check_text(where, r.get("name"), ok, errs)
        key = str(r.get("name")).casefold()
        if key in texts:
            errs.append(f"{where}: duplicate name {r.get('name')!r}")
        texts.add(key)
        note = r.get("note") or ""
        if note:
            _check_text(where + ".note", note, ok, errs)
        names.append(Name(str(r.get("id")), str(r.get("name")), _tags(where, r, errs), status,
                          bool(r.get("needs_owner", False)), note))
    vocab = frozenset(t for n in names if n.status == "approved" for t in n.tags)

    taglines, seen = [], set()
    for r in _dicts("taglines", raw["taglines"], errs):
        where, status, ok = _common("taglines", r, seen, errs)
        _check_text(where, r.get("text"), ok, errs)
        taglines.append(Tagline(str(r.get("id")), str(r.get("text")), status))

    questions, seen = [], set()
    for r in _dicts("questions", raw["questions"], errs):
        where, status, ok = _common("questions", r, seen, errs)
        if r.get("type") not in QUESTION_TYPES:
            errs.append(f"{where}: type must be one of {sorted(QUESTION_TYPES)}")
        fixed = r.get("fixed")
        if fixed not in (None, "first_burn"):
            errs.append(f"{where}: fixed must be 'first_burn' or absent")
        _check_text(where + ".prompt", r.get("prompt"), ok, errs)
        quip = r.get("quip") or ""
        if quip:
            _check_text(where + ".quip", quip, ok, errs)
        opts, oseen = [], set()
        rows = _dicts(where + ".options", r.get("options"), errs)
        if len(rows) < 2:
            errs.append(f"{where}: needs at least 2 options")
        for o in rows:
            ow = f"{where}.options[{o.get('id')}]"
            if o.get("id") in oseen:
                errs.append(f"{ow}: duplicate option id")
            oseen.add(o.get("id"))
            _check_text(ow, o.get("text"), ok, errs)
            otags = _tags(ow, o, errs)
            if ok and (unknown := otags - vocab):
                errs.append(f"{ow}: tags {sorted(unknown)} are not on any approved name")
            fb = o.get("first_burn")
            if fb is not None and not isinstance(fb, int):
                errs.append(f"{ow}: first_burn must be an integer")
                fb = None
            opts.append(Option(str(o.get("id")), str(o.get("text")), bool(o.get("correct", False)), otags, fb))
        # ponytail: a question with no correct option is allowed only as a pure preference question
        # (every option tagged), which the sample content relies on.
        if not fixed and opts and not any(o.correct for o in opts) and not all(o.tags for o in opts):
            errs.append(f"{where}: needs a correct option (or tag every option for a preference question)")
        questions.append(Question(str(r.get("id")), str(r.get("type")), str(r.get("prompt")), opts,
                                  quip, str(status), bool(r.get("needs_owner", False)), fixed))
    if sum(q.fixed == "first_burn" for q in questions) != 1:
        errs.append("questions: exactly one question must have fixed: first_burn")

    if errs:
        raise ContentError("\n".join(errs))
    return Content(names, questions, taglines)


def _read_raw(dir: Path) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for kind in KINDS:
        path = Path(dir) / f"{kind}.yaml"
        try:
            raw[kind] = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise ContentError(f"{path.name}: cannot read/parse: {e}") from e
        if raw[kind] is None:
            raw[kind] = []
    return raw


def load_content(dir: Path) -> Content:
    return _build(_read_raw(dir))


def _commit(dir: Path, kind: str, raw: dict[str, Any]) -> None:
    _build(raw)  # validate the would-be result before touching disk
    path = Path(dir) / f"{kind}.yaml"
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{kind}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw[kind], f, sort_keys=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)  # mkstemp makes 0600
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ContentError(f"unknown kind {kind!r}; expected one of {KINDS}")


def set_status(dir: Path, kind: Kind, id: str, status: str) -> None:
    _check_kind(kind)
    raw = _read_raw(dir)
    entry = next((r for r in raw[kind] if isinstance(r, dict) and r.get("id") == id), None)
    if entry is None:
        raise ContentError(f"{kind}[{id}]: no such entry")
    entry["status"] = status  # _commit rejects an invalid status
    _commit(dir, kind, raw)


def append_entry(dir: Path, kind: Kind, entry: dict) -> None:
    _check_kind(kind)
    raw = _read_raw(dir)
    raw[kind].append(dict(entry))
    _commit(dir, kind, raw)


def _summary(c: Content) -> str:
    lines = []
    for kind, items in (("names", c.names), ("questions", c.questions), ("taglines", c.taglines)):
        by = Counter(i.status for i in items)
        lines.append(f"{kind}: {len(items)} ({', '.join(f'{k}={v}' for k, v in sorted(by.items()))})")
    tags = Counter(t for n in c.approved_names for t in n.tags)
    lines.append("approved name tags: " + ", ".join(f"{t}={n}" for t, n in sorted(tags.items())))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m app.content <content-dir>", file=sys.stderr)
        return 2
    try:
        print(_summary(load_content(Path(argv[1]))))
    except ContentError as e:
        print(f"{e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
