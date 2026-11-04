import os
import shutil
import subprocess

import pytest

from app.config import ROOT

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def _dry_run(tmp_path, **env):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "deploy", repo / "deploy")
    shutil.copy(ROOT / ".env.example", repo / ".env.example")
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
    assert sorted(p.name for p in repo.iterdir()) == [".env.example", "deploy"]  # nothing written


def test_dry_run_default_port_keeps_bind_capability_and_hostname(tmp_path):
    _, r = _dry_run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "AmbientCapabilities=CAP_NET_BIND_SERVICE" in r.stdout
    assert "DRY-RUN: sudo hostnamectl set-hostname babyburn" in r.stdout
    assert "DRY-RUN: sudo timedatectl set-timezone America/New_York" in r.stdout


def test_bad_port_rejected(tmp_path):
    _, r = _dry_run(tmp_path, KIOSK_PORT="70000")
    assert r.returncode != 0 and "KIOSK_PORT" in r.stderr
