import subprocess

import pytest

from app import admin
from test_admin import AUTH, POST, k  # noqa: F401  (k is the fixture)

APP = "baby-burn-name-generator"


class Fake:
    """Stands in for subprocess: records argv, answers `status` with canned text."""
    def __init__(self, status="deployed abc123\nPAUSED: party", rc=0):
        self.calls, self.popens, self.status, self.rc = [], [], status, rc

    def run(self, argv, **kw):
        self.calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, self.rc, self.status if argv[1] == "status" else "", "")

    def Popen(self, argv, **kw):
        self.popens.append((argv, kw))


@pytest.fixture
def gd(monkeypatch):
    f = Fake()
    monkeypatch.setattr(admin.shutil, "which", lambda n: "/usr/bin/git-deploy" if n == "git-deploy" else None)
    monkeypatch.setattr(admin.subprocess, "run", f.run)
    monkeypatch.setattr(admin.subprocess, "Popen", f.Popen)
    return f


def test_panel_hidden_with_note_when_not_installed(k, monkeypatch):
    monkeypatch.setattr(admin.shutil, "which", lambda n: None)
    body = k.c.get("/admin", headers=AUTH).get_data(as_text=True)
    assert "git-deploy is not installed" in body and "/admin/update/pause" not in body


def test_panel_shows_status_and_paused_line(k, gd):
    body = k.c.get("/admin", headers=AUTH).get_data(as_text=True)
    assert "deployed abc123" in body and "PAUSED: party" in body
    argv, kw = gd.calls[0]
    assert argv == ["/usr/bin/git-deploy", "status", APP] and kw["timeout"] > 0 and not kw.get("shell")
    assert "/admin/update/pause" in body and "/admin/update/sync" in body


def test_status_timeout_is_reported_not_a_500(k, gd, monkeypatch):
    def boom(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)
    monkeypatch.setattr(admin.subprocess, "run", boom)
    assert k.c.get("/admin", headers=AUTH).status_code == 200


def test_pause_and_resume_run_git_deploy(k, gd):
    assert k.c.post("/admin/update/pause", headers=POST).get_json() == {"ok": True}
    assert k.c.post("/admin/update/resume", headers=POST).get_json() == {"ok": True}
    assert gd.calls[0][0] == ["/usr/bin/git-deploy", "pause", APP, "--reason", "paused from admin"]
    assert gd.calls[1][0] == ["/usr/bin/git-deploy", "resume", APP]


def test_failed_command_is_a_json_error(k, gd):
    gd.rc, gd.status = 1, ""
    r = k.c.post("/admin/update/pause", headers=POST)
    assert r.status_code == 502 and "error" in r.get_json()


def test_sync_is_detached_and_logs_under_data(k, gd):
    assert k.c.post("/admin/update/sync", headers=POST).get_json() == {"ok": True}
    [(argv, kw)] = gd.popens
    assert argv == ["/usr/bin/git-deploy", "sync", APP, "--force"]
    assert kw["start_new_session"] is True and not kw.get("shell")
    assert kw["stdout"].name.startswith(str(k.cfg.data_dir))


def test_unknown_action_404_and_pin_and_origin_enforced(k, gd):
    assert k.c.post("/admin/update/rm", headers=POST).status_code == 404
    assert k.c.post("/admin/update/pause").status_code == 401
    assert k.c.post("/admin/update/pause", headers={**AUTH, "Origin": "http://evil.example"}).status_code == 403
    assert not gd.calls


def test_actions_refused_when_not_installed(k, monkeypatch):
    monkeypatch.setattr(admin.shutil, "which", lambda n: None)
    assert k.c.post("/admin/update/sync", headers=POST).status_code == 503
