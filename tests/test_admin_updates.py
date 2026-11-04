import os
import pwd
import re
import subprocess

import pytest

from app import admin
from test_admin import AUTH, POST, k  # noqa: F401  (k is the fixture)

APP = "baby-burn-name-generator"


class Fake:
    """Stands in for subprocess: records argv, answers `status` with canned text."""
    def __init__(self, status="deployed abc123\n  PAUSED (since 2026-11-03): party", rc=0):
        self.calls, self.status, self.rc = [], status, rc

    def run(self, argv, **kw):
        self.calls.append((argv, kw))
        return subprocess.CompletedProcess(argv, self.rc, self.status if argv[1] == "status" else "", "")


@pytest.fixture
def gd(monkeypatch):
    f = Fake()
    monkeypatch.setattr(admin.shutil, "which", lambda n: "/usr/bin/git-deploy" if n == "git-deploy" else None)
    monkeypatch.setattr(admin.subprocess, "run", f.run)
    monkeypatch.setattr(admin, "_status_cache", {"at": float("-inf"), "text": ""})
    return f


def _timeout(argv, **kw):
    raise subprocess.TimeoutExpired(argv, 1)


def test_panel_hidden_with_note_when_not_installed(k, monkeypatch):
    monkeypatch.setattr(admin.shutil, "which", lambda n: None)
    body = k.c.get("/admin", headers=AUTH).get_data(as_text=True)
    assert "git-deploy is not installed" in body and "/admin/update/pause" not in body


def test_panel_shows_status_and_paused_line(k, gd):
    body = k.c.get("/admin", headers=AUTH).get_data(as_text=True)
    assert "deployed abc123" in body and 'class="banner warn">PAUSED (since 2026-11-03): party' in body
    argv, kw = gd.calls[0]
    assert argv == ["/usr/bin/git-deploy", "status", APP] and kw["timeout"] > 0 and not kw.get("shell")
    assert "/admin/update/pause" in body and "/admin/update/sync" in body


def test_blocked_line_highlighted_and_substring_ignored(k, gd):
    gd.status = "app: /x\n  BLOCKED: unhealthy at abc1234\n  last: fix UNPAUSED banner"
    body = k.c.get("/admin", headers=AUTH).get_data(as_text=True)
    assert 'class="banner warn">BLOCKED: unhealthy at abc1234' in body
    assert 'class="banner warn">  last' not in body and 'banner warn">last' not in body


def test_status_timeout_says_deploy_running(k, gd, monkeypatch):
    monkeypatch.setattr(admin.subprocess, "run", _timeout)
    r = k.c.get("/admin", headers=AUTH)
    assert r.status_code == 200 and admin.BUSY_MSG in r.get_data(as_text=True)


def test_status_cached_briefly_and_dropped_after_an_action(k, gd):
    k.c.get("/admin", headers=AUTH)
    k.c.get("/admin", headers=AUTH)
    assert [c[0][1] for c in gd.calls] == ["status"]
    k.c.post("/admin/update/pause", headers=POST)
    k.c.get("/admin", headers=AUTH)
    assert [c[0][1] for c in gd.calls] == ["status", "pause", "status"]


@pytest.mark.parametrize("action", ["pause", "resume"])
def test_action_timeout_says_deploy_running(k, gd, monkeypatch, action):
    monkeypatch.setattr(admin.subprocess, "run", _timeout)
    r = k.c.post(f"/admin/update/{action}", headers=POST)
    assert r.status_code == 504 and r.get_json()["error"] == admin.BUSY_MSG


def test_pause_and_resume_run_git_deploy(k, gd):
    assert k.c.post("/admin/update/pause", headers=POST).get_json() == {"ok": True}
    assert k.c.post("/admin/update/resume", headers=POST).get_json() == {"ok": True}
    assert gd.calls[0][0] == ["/usr/bin/git-deploy", "pause", APP, "--reason", "paused from admin"]
    assert gd.calls[1][0] == ["/usr/bin/git-deploy", "resume", APP]


def test_failed_command_is_a_json_error(k, gd):
    gd.rc, gd.status = 1, ""
    r = k.c.post("/admin/update/pause", headers=POST)
    assert r.status_code == 502 and "error" in r.get_json()


def test_sync_runs_in_its_own_transient_unit(k, gd):
    """The deploy restarts app-babyburn, so it must run outside its cgroup (a systemd-run unit)."""
    assert k.c.post("/admin/update/sync", headers=POST).get_json() == {"ok": True}
    [(argv, kw)] = gd.calls
    log = k.cfg.data_dir / "git-deploy-sync.log"
    me = pwd.getpwuid(os.getuid())
    assert argv[:6] == ["sudo", "-n", "systemd-run", f"--uid={me.pw_name}", "--collect", "--quiet"]
    assert re.fullmatch(r"--unit=babyburn-update-\d{8}-\d{6}", argv[6])
    assert argv[7:] == [f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
                        f"--working-directory={me.pw_dir}", f"--setenv=HOME={me.pw_dir}",
                        "/usr/bin/git-deploy", "sync", APP, "--force"]
    assert kw["timeout"] > 0 and not kw.get("shell") and log.exists()


def test_update_now_message_promises_no_success(k, gd):
    body = k.c.get("/admin", headers=AUTH).get_data(as_text=True)
    assert "Update started — the kiosk restarts if there's a new version; check status in a minute" in body


def test_unknown_action_404_and_pin_and_origin_enforced(k, gd):
    assert k.c.post("/admin/update/rm", headers=POST).status_code == 404
    assert k.c.post("/admin/update/pause").status_code == 401
    assert k.c.post("/admin/update/pause", headers={**AUTH, "Origin": "http://evil.example"}).status_code == 403
    assert not gd.calls


def test_actions_refused_when_not_installed(k, monkeypatch):
    monkeypatch.setattr(admin.shutil, "which", lambda n: None)
    assert k.c.post("/admin/update/sync", headers=POST).status_code == 503
