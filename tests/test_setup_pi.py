import os
import shutil
import subprocess

import pytest

from app.config import ROOT

pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available"),
    pytest.mark.skipif(os.geteuid() == 0, reason="setup-pi.sh refuses to run as root"),
]


def _dry_run(tmp_path, dotenv=None, **env):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy", repo / "deploy")
    shutil.copy(ROOT / ".env.example", repo / ".env.example")
    if dotenv is not None:
        (repo / ".env").write_text(dotenv)
    e = {**os.environ, "DRY_RUN": "1", **env}
    return repo, subprocess.run(["bash", str(repo / "deploy/setup-pi.sh")], env=e, cwd=repo,
                                capture_output=True, text=True)


def test_script_syntax():
    subprocess.run(["bash", "-n", str(ROOT / "deploy/setup-pi.sh")], check=True)


def test_dry_run_alongside_changes_nothing(tmp_path):
    repo, r = _dry_run(tmp_path, SET_HOSTNAME="no", SET_TIMEZONE="no", KIOSK_HOST="x", KIOSK_PORT="9443")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "hostnamectl" not in out and "timedatectl" not in out
    assert "hostname and /etc/hosts untouched" in out and "timezone untouched" in out
    assert "LISTEN_PORT=9443" in out and "TLS_CERT=certs/x.local.pem" in out
    unit = out.split("babyburn.service:")[1].split("DRY-RUN: sudo systemctl")[0]
    assert "AmbientCapabilities" not in unit  # port >= 1024
    assert f"WorkingDirectory={repo}" in unit and "@" not in unit
    assert "DRY-RUN: sudo systemctl enable babyburn.service" in out
    assert "--subsystem-match=usb --attr-match=idVendor=04f9" in out
    assert sorted(p.name for p in repo.iterdir()) == [".env.example", "deploy"]  # nothing written


def test_dry_run_default_port_keeps_bind_capability_and_hostname(tmp_path):
    _, r = _dry_run(tmp_path, KIOSK_HOST="kiosk-test-host")  # not this machine's hostname
    assert r.returncode == 0, r.stderr
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in r.stdout
    assert "DRY-RUN: sudo hostnamectl set-hostname kiosk-test-host" in r.stdout
    assert "DRY-RUN: sudo timedatectl set-timezone America/New_York" in r.stdout


def test_bad_port_rejected(tmp_path):
    _, r = _dry_run(tmp_path, KIOSK_PORT="70000")
    assert r.returncode != 0 and "KIOSK_PORT" in r.stderr


@pytest.mark.parametrize("bad", ["2", "yes", ""])
def test_dry_run_value_must_be_0_or_1(tmp_path, bad):
    _, r = _dry_run(tmp_path, DRY_RUN=bad)
    assert r.returncode != 0 and "DRY_RUN" in r.stderr and "DRY-RUN" not in r.stdout


def test_app_user_must_be_current_user(tmp_path):
    _, r = _dry_run(tmp_path, APP_USER="someone-else")
    assert r.returncode != 0 and "APP_USER" in r.stderr


def test_rerun_warns_when_env_differs(tmp_path):
    _, r = _dry_run(tmp_path, dotenv="LISTEN_PORT=1111\nTLS_CERT=certs/x.local.pem\n",
                    KIOSK_HOST="x", KIOSK_PORT="9443", SET_HOSTNAME="no", SET_TIMEZONE="no")
    assert r.returncode == 0, r.stderr
    assert "WARNING: .env keeps LISTEN_PORT=1111" in r.stdout and "imply 9443" in r.stdout
    assert "WARNING: .env keeps TLS_CERT" not in r.stdout  # same value: no warning
