"""Load, validate and edit the party content (names, questions, taglines) in YAML."""

import os
import re
import stat
import string
import sys
import tempfile
import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

Kind = Literal["names", "questions", "taglines"]
KINDS: tuple[str, ...] = get_args(Kind)
STATUSES = frozenset({"draft", "approved", "rejected"})
QUESTION_TYPES = frozenset({"single", "multi"})
# Max characters per field so badge and kiosk text always fit.
MAX_NAME, MAX_TAGLINE, MAX_OPTION, MAX_QUIP, MAX_PROMPT = 40, 32, 55, 60, 200

# Safety net only: drug terms, real politicians, party names. The owner's review is the real gate.
BANNED_WORDS: frozenset[str] = frozenset({
    "cocaine", "heroin", "meth", "methamphetamine", "lsd", "mdma", "ketamine",
    "marijuana", "cannabis", "shrooms", "psilocybin", "opium", "fentanyl", "dmt", "ayahuasca",
    "trump", "biden", "obama", "reagan", "putin", "republican", "republicans", "democrat", "democrats",
    "gop", "maga",
})

_LOCK = threading.Lock()  # serializes read-modify-write in set_status/append_entry (web threads)
# libyaml's C loader/dumper when PyYAML was built with it: ~5x faster on the Pi 3, where the pure-Python
# parser made each admin Approve/Reject take ~5 s. Same safe subset of YAML either way.
_Loader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)
_Dumper = getattr(yaml, "CSafeDumper", yaml.SafeDumper)
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
    warnings: list[str] = field(default_factory=list)  # non-blocking findings for the owner

    @property
    def approved_names(self) -> list[Name]:
        return [n for n in self.names if n.status == "approved"]

    @property
    def approved_questions(self) -> list[Question]:
        return [q for q in self.questions if q.status == "approved" and not q.fixed]

    @property
    def first_burn_question(self) -> Question | None:
        """The fixed first-burn question, or None while it is not approved."""
        q = next(q for q in self.questions if q.fixed == "first_burn")
        return q if q.status == "approved" else None

    @property
    def approved_taglines(self) -> list[Tagline]:
        return [t for t in self.taglines if t.status == "approved"]

    @property
    def tag_vocab(self) -> frozenset[str]:
        return frozenset(t for n in self.approved_names for t in n.tags)


def _renderable(ch: str) -> bool:
    return 0x20 <= ord(ch) <= 0x7E or 0xA0 <= ord(ch) <= 0xFF or ch in _ALLOWED_EXTRA


def _check_text(where: str, text: Any, approved: bool, errs: list[str], max_len: int) -> None:
    if not isinstance(text, str) or not text.strip():
        errs.append(f"{where}: text must be a non-empty string")
        return
    if len(text) > max_len:
        errs.append(f"{where}: at most {max_len} characters, got {len(text)}")
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
    return frozenset(t.lower() for t in tags)  # tags match case-insensitively


def _build(raw: dict[str, Any]) -> Content:
    errs: list[str] = []
    warnings: list[str] = []

    names, seen, texts = [], set(), set()
    for r in _dicts("names", raw["names"], errs):
        where, status, ok = _common("names", r, seen, errs)
        if str(r.get("id")).startswith(RESERVED_NAME_PREFIX):
            errs.append(f"{where}: id may not start with {RESERVED_NAME_PREFIX!r} (reserved for fallback names)")
        _check_text(where, r.get("name"), ok, errs, MAX_NAME)
        key = str(r.get("name")).casefold()
        if key in texts:
            errs.append(f"{where}: duplicate name {r.get('name')!r}")
        texts.add(key)
        note = r.get("note") or ""
        if note:
            _check_text(where + ".note", note, ok, errs, 500)
        names.append(Name(str(r.get("id")), str(r.get("name")), _tags(where, r, errs), status,
                          bool(r.get("needs_owner", False)), note))
    vocab = frozenset(t for n in names if n.status == "approved" for t in n.tags)

    taglines, seen = [], set()
    for r in _dicts("taglines", raw["taglines"], errs):
        where, status, ok = _common("taglines", r, seen, errs)
        _check_text(where, r.get("text"), ok, errs, MAX_TAGLINE)
        taglines.append(Tagline(str(r.get("id")), str(r.get("text")), status))

    questions, seen = [], set()
    for r in _dicts("questions", raw["questions"], errs):
        where, status, ok = _common("questions", r, seen, errs)
        if r.get("type") not in QUESTION_TYPES:
            errs.append(f"{where}: type must be one of {sorted(QUESTION_TYPES)}")
        fixed = r.get("fixed")
        if fixed not in (None, "first_burn"):
            errs.append(f"{where}: fixed must be 'first_burn' or absent")
        _check_text(where + ".prompt", r.get("prompt"), ok, errs, MAX_PROMPT)
        quip = r.get("quip") or ""
        if quip:
            _check_text(where + ".quip", quip, ok, errs, MAX_QUIP)
        opts, oseen = [], set()
        rows = _dicts(where + ".options", r.get("options"), errs)
        if len(rows) < 2:
            errs.append(f"{where}: needs at least 2 options")
        for o in rows:
            ow = f"{where}.options[{o.get('id')}]"
            if o.get("id") in oseen:
                errs.append(f"{ow}: duplicate option id")
            oseen.add(o.get("id"))
            _check_text(ow, o.get("text"), ok, errs, MAX_OPTION)
            otags = _tags(ow, o, errs)
            if ok and (unknown := otags - vocab):
                warnings.append(f"{ow}: tags {sorted(unknown)} are not on any approved name")
            fb = o.get("first_burn")
            if fb is not None and (not isinstance(fb, int) or isinstance(fb, bool)):
                errs.append(f"{ow}: first_burn must be an integer")
                fb = None
            opts.append(Option(str(o.get("id")), str(o.get("text")), bool(o.get("correct", False)), otags, fb))
        # ceiling: a question with no correct option is allowed only as a pure preference question
        # (every option tagged), which the sample content relies on.
        if not fixed and opts and not any(o.correct for o in opts) and not all(o.tags for o in opts):
            errs.append(f"{where}: needs a correct option (or tag every option for a preference question)")
        if r.get("type") == "single" and sum(o.correct for o in opts) > 1:
            errs.append(f"{where}: a single-choice question needs exactly one correct option")
        questions.append(Question(str(r.get("id")), str(r.get("type")), str(r.get("prompt")), opts,
                                  quip, str(status), bool(r.get("needs_owner", False)), fixed))
    if sum(q.fixed == "first_burn" for q in questions) != 1:
        errs.append("questions: exactly one question must have fixed: first_burn")

    if errs:
        raise ContentError("\n".join(errs))
    return Content(names, questions, taglines, warnings)


def _read_raw(dir: Path) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    for kind in KINDS:
        path = Path(dir) / f"{kind}.yaml"
        try:
            raw[kind] = yaml.load(path.read_text(encoding="utf-8"), Loader=_Loader)
        except (OSError, yaml.YAMLError) as e:
            raise ContentError(f"{path.name}: cannot read/parse: {e}") from e
        if raw[kind] is None:
            raw[kind] = []
    return raw


def load_content(dir: Path) -> Content:
    return _build(_read_raw(dir))


def _commit(dir: Path, kind: str, raw: dict[str, Any]) -> Content:
    """Validate, write raw[kind] atomically, and return the Content now on disk (raw holds all three kinds)."""
    content = _build(raw)  # validate the would-be result before touching disk
    path = Path(dir) / f"{kind}.yaml"
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{kind}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.dump(raw[kind], f, Dumper=_Dumper, sort_keys=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)  # mkstemp makes 0600
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return content


def _check_kind(kind: str) -> None:
    if kind not in KINDS:
        raise ContentError(f"unknown kind {kind!r}; expected one of {KINDS}")


def set_status(dir: Path, kind: Kind, ids: str | list[str], status: str) -> Content:
    """Set the status of one id, or of every listed id in one write; any unknown id writes nothing.
    Returns the content as written, same as a fresh load_content(dir)."""
    _check_kind(kind)
    with _LOCK:
        raw = _read_raw(dir)
        by_id = {r.get("id"): r for r in raw[kind] if isinstance(r, dict)}
        for id in [ids] if isinstance(ids, str) else ids:
            if not isinstance(id, str) or id not in by_id:
                raise ContentError(f"{kind}[{id}]: no such entry")
            by_id[id]["status"] = status  # _commit rejects an invalid status
        return _commit(dir, kind, raw)


def append_entry(dir: Path, kind: Kind, entry: dict) -> Content:
    _check_kind(kind)
    with _LOCK:
        raw = _read_raw(dir)
        raw[kind].append(dict(entry))
        return _commit(dir, kind, raw)


_EDITABLE = {"names": {"name", "tags"}, "taglines": {"text"}, "questions": {"prompt", "type", "quip", "options"}}
_OPTION_KEYS = {"id", "text", "correct", "tags"}


def _str(where: str, v: Any) -> str:
    if not isinstance(v, str):
        raise ContentError(f"{where}: must be a string")
    return v.strip()


def _tag_list(where: str, v: Any) -> list[str]:
    if not isinstance(v, list) or not all(isinstance(t, str) for t in v):
        raise ContentError(f"{where}: tags must be a list of strings")
    return list(dict.fromkeys(t.strip().lower() for t in v if t.strip()))  # deduped, order kept


def _edit_options(where: str, old: list[dict], new: Any, fixed: bool) -> list[dict]:
    """The question's new option list: existing options keep their id and any other keys."""
    most = max(4, len(old))  # never force dropping options a question already has
    if not isinstance(new, list) or not all(isinstance(o, dict) for o in new) or not 2 <= len(new) <= most:
        raise ContentError(f"{where}: options must be a list of 2-{most} objects")
    by_id = {o.get("id"): o for o in old}
    given = [o.get("id") for o in new if "id" in o]
    if not all(isinstance(i, str) for i in given) or len(given) != len(set(given)) or not set(given) <= by_id.keys():
        raise ContentError(f"{where}: option ids must be existing, distinct ids of this question")
    if fixed and [o.get("id") for o in new] != [o.get("id") for o in old]:  # same count, ids and order
        raise ContentError(f"{where}: the first-burn question's options are fixed; only their text can change")
    free = iter(s for s in string.ascii_lowercase if s not in by_id)  # never reuse an old (maybe removed) id
    out = []
    for o in new:
        if bad := sorted(set(o) - _OPTION_KEYS):
            raise ContentError(f"{where}: option fields {bad} cannot be edited")
        cur = dict(by_id.get(o.get("id")) or {"id": next(free)})
        cur["text"] = _str(f"{where}.text", o.get("text"))
        correct, tags = o.get("correct", False), _tag_list(where, o.get("tags", []))
        if not isinstance(correct, bool):
            raise ContentError(f"{where}: correct must be true or false")
        if fixed:
            if correct != bool(cur.get("correct")) or sorted(tags) != sorted(_tag_list(where, cur.get("tags") or [])):
                raise ContentError(f"{where}: the first-burn question's options are fixed; only their text can change")
        else:
            cur.pop("correct", None)
            cur.pop("tags", None)
            if correct:
                cur["correct"] = True
            if tags:
                cur["tags"] = tags
        out.append(cur)
    return out


def update_entry(dir: Path, kind: Kind, id: str, fields: dict) -> Content:
    """Edit the owner-editable fields of one entry (never id or status); any problem writes nothing."""
    _check_kind(kind)
    if not isinstance(fields, dict):
        raise ContentError("fields must be an object")
    where = f"{kind}[{id}]"
    if bad := sorted(set(fields) - _EDITABLE[kind]):
        raise ContentError(f"{where}: fields {bad} cannot be edited")
    with _LOCK:
        raw = _read_raw(dir)
        r = next((r for r in raw[kind] if isinstance(r, dict) and r.get("id") == id), None)
        if r is None:
            raise ContentError(f"{where}: no such entry")
        fixed = bool(r.get("fixed"))
        for k, v in fields.items():
            if k == "tags":
                r["tags"] = _tag_list(where, v)
            elif k == "options":
                r["options"] = _edit_options(where, [o for o in r.get("options") or [] if isinstance(o, dict)], v, fixed)
            elif k == "quip":
                if quip := _str(where + ".quip", v):
                    r["quip"] = quip
                else:
                    r.pop("quip", None)
            elif k == "type" and fixed and v != r.get("type"):
                raise ContentError(f"{where}: the first-burn question's type is fixed")
            else:
                r[k] = _str(f"{where}.{k}", v)  # name, text, prompt, type: _commit validates the values
        return _commit(dir, kind, raw)


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
        c = load_content(Path(argv[1]))
        print(_summary(c))
        for w in c.warnings:
            print(f"warning: {w}", file=sys.stderr)
    except ContentError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
