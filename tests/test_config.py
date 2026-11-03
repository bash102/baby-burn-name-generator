import pytest

from app.config import Config


def test_bad_numeric_names_variable():
    with pytest.raises(ValueError, match="QUIZ_RANDOM_COUNT"):
        Config.from_env({"QUIZ_RANDOM_COUNT": ""})


def test_content_dir_falls_back_to_sample():
    assert Config.from_env({"CONTENT_DIR": "/nope"}).content_dir.name == "content.sample"


def test_defaults():
    c = Config.from_env({})
    assert c.transform_provider == "fake"
    assert c.printer == "file"
    assert c.transform_timeout_s == 25
    assert c.s3_endpoint_url is None
    assert c.photo_retention_days == 14


def test_env_overrides_and_numeric_parsing(tmp_path):
    c = Config.from_env({
        "CONTENT_DIR": str(tmp_path),
        "TRANSFORM_PROVIDER": "openai",
        "TRANSFORM_TIMEOUT_S": "7.5",
        "QUIZ_RANDOM_COUNT": "2",
        "S3_ENDPOINT_URL": "https://x.r2.example",
    })
    assert c.content_dir == tmp_path
    assert c.transform_provider == "openai"
    assert c.transform_timeout_s == 7.5
    assert c.quiz_random_count == 2
    assert c.s3_endpoint_url == "https://x.r2.example"
