from pathlib import Path

import pytest

from noah.config import ConfigError, load_settings


def _load(tmp_path: Path, toml: str | None = None, **env: str):
    config_path = tmp_path / "config.toml"
    if toml is not None:
        config_path.write_text(toml)
    return load_settings(config_path=config_path, env={"XDG_DATA_HOME": str(tmp_path), **env})


def test_defaults_when_no_config_file(tmp_path: Path):
    settings = _load(tmp_path)
    assert not settings.config_found
    assert settings.llm.base_url == "https://openrouter.ai/api/v1"
    assert settings.llm.max_steps == 8
    assert settings.tasks.verbosity == "normal"
    assert settings.data_dir == tmp_path / "noah"
    assert settings.openrouter_api_key is None


def test_config_file_overrides_defaults(tmp_path: Path):
    settings = _load(tmp_path, '[llm]\nmodel = "some/model"\n[tasks]\nverbosity = "quiet"\n')
    assert settings.config_found
    assert settings.llm.model == "some/model"
    assert settings.llm.base_url == "https://openrouter.ai/api/v1"
    assert settings.tasks.verbosity == "quiet"


def test_api_key_comes_from_env(tmp_path: Path):
    settings = _load(tmp_path, OPENROUTER_API_KEY="sk-test")
    assert settings.openrouter_api_key is not None
    assert settings.openrouter_api_key.get_secret_value() == "sk-test"
    assert "sk-test" not in repr(settings)


def test_noah_config_env_var_picks_file(tmp_path: Path):
    custom = tmp_path / "custom.toml"
    custom.write_text('[llm]\nmodel = "from/env-path"\n')
    settings = load_settings(env={"NOAH_CONFIG": str(custom)})
    assert settings.config_path == custom
    assert settings.llm.model == "from/env-path"


@pytest.mark.parametrize(
    "toml",
    [
        "[llm\n",  # bad TOML
        '[llm]\nmodle = "typo"\n',  # unknown key
        '[tasks]\nverbosity = "loud"\n',  # bad value
        "[llm]\nmax_steps = 0\n",  # out of range
    ],
)
def test_invalid_config_raises(tmp_path: Path, toml: str):
    with pytest.raises(ConfigError):
        _load(tmp_path, toml)
