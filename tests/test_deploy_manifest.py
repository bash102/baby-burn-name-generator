import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IN_USE = {22, 443, 8000, 8443, 8787, 8788, 9000, 9443}
M = tomllib.loads((ROOT / "deploy" / "pi.toml").read_text())


def test_required_keys():
    assert re.fullmatch(r"[a-z0-9-]+", M["service"])
    for key in ("start", "health"):
        assert M[key].strip()
    assert isinstance(M["port"], int) and isinstance(M["serve_port"], int)


def test_ports_free_and_consistent():
    assert not {M["port"], M["serve_port"]} & IN_USE
    assert M["port"] != M["serve_port"]
    assert M["health"].startswith(f"http://127.0.0.1:{M['port']}/")
    assert M["env"]["LOCAL_HTTP_PORT"] == str(M["port"])  # the app must listen where git-deploy proxies


def test_env_file_outside_repo():
    p = M["env_file"]
    assert p.startswith(("~/", "/")) and not (ROOT / p).exists()
    assert not Path(p).expanduser().is_relative_to(ROOT)
