import shutil
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parent.parent / "content.sample"


@pytest.fixture
def sample_content_dir(tmp_path) -> Path:
    dest = tmp_path / "content"
    shutil.copytree(SAMPLE, dest)
    return dest
