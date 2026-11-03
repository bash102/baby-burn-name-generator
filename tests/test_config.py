import pytest

from app.config import ROOT, Config


def test_empty_numeric_names_variable():
    with pytest.raises(ValueError, match="QUIZ_RANDOM_COUNT"):
        Config.from_env({"QUIZ_RANDOM_COUNT": ""})


@pytest.mark.parametrize("var,val", [
    ("QUIZ_RANDOM_COUNT", "0"),
    ("SESSION_TTL_S", "-5"),
    ("TRANSFORM_TIMEOUT_S", "inf"),
    ("TRANSFORM_TIMEOUT_S", "nan"),
    ("PHOTO_RETENTION_DAYS", "abc"),
])
def test_bad_numeric_raises(var, val):
    with pytest.raises(ValueError, match=var):
        Config.from_env({var: val})


def test_unknown_provider_raises():
    with pytest.raises(ValueError, match="TRANSFORM_PROVIDER"):
        Config.from_env({"TRANSFORM_PROVIDER": "dalle"})


def test_missing_content_dir_raises():
    with pytest.raises(ValueError, match="CONTENT_DIR.*/nope"):
        Config.from_env({"CONTENT_DIR": "/nope"})


def test_unset_content_dir_defaults_to_sample_when_no_private_content():
    c = Config.from_env({})
    expected = ROOT / "content" if (ROOT / "content").is_dir() else ROOT / "content.sample"
    assert c.content_dir == expected


def test_empty_content_dir_is_unset():
    assert Config.from_env({"CONTENT_DIR": ""}) == Config.from_env({})


def test_content_is_sample():
    assert Config.from_env({"CONTENT_DIR": "content.sample"}).content_is_sample


def test_content_is_not_sample(tmp_path):
    assert not Config.from_env({"CONTENT_DIR": str(tmp_path)}).content_is_sample


def test_relative_paths_resolve_against_root_not_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = Config.from_env({"CONTENT_DIR": "content.sample", "DATA_DIR": "somedata"})
    assert c.content_dir == ROOT / "content.sample"
    assert c.data_dir == ROOT / "somedata"
    assert Config.from_env({}).data_dir == ROOT / "data"


def test_absolute_data_dir_kept(tmp_path):
    assert Config.from_env({"DATA_DIR": str(tmp_path)}).data_dir == tmp_path


def test_defaults():
    c = Config.from_env({})
    assert c.transform_provider == "fake"
    assert c.printer == "file"
    assert c.transform_timeout_s == 25
    assert c.s3_endpoint_url is None
    assert c.photo_retention_days == 14


def test_empty_s3_endpoint_is_none():
    assert Config.from_env({"S3_ENDPOINT_URL": ""}).s3_endpoint_url is None


def test_photo_base_url_trailing_slash_stripped():
    assert Config.from_env({"PHOTO_BASE_URL": "https://p.example.com//"}).photo_base_url == "https://p.example.com"


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
