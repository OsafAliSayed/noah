"""Settings, loaded from config.toml with API keys from the environment or .env files.

Precedence, highest first: real environment variables, the project .env,
~/.config/noah/.env, config.toml, then the defaults below.
"""

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from dotenv import dotenv_values
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError


def _xdg_dir(env_var: str, fallback: str, env: Mapping[str, str | None]) -> Path:
    base = env.get(env_var) or str(Path.home() / fallback)
    return Path(base) / "noah"


class _Section(BaseModel):
    # Reject unknown keys so a typo in config.toml is an error, not silently ignored.
    model_config = ConfigDict(extra="forbid")


class LLMSettings(_Section):
    base_url: str = "https://openrouter.ai/api/v1"
    # Placeholder until M4 picks a default by benchmark. OpenRouter's auto router picks per request.
    model: str = "openrouter/auto"
    max_steps: int = Field(default=8, ge=1)


class TasksSettings(_Section):
    verbosity: Literal["quiet", "normal", "chatty"] = "normal"


class Settings(_Section):
    llm: LLMSettings = LLMSettings()
    tasks: TasksSettings = TasksSettings()
    data_dir: Path
    state_dir: Path
    openrouter_api_key: SecretStr | None = None
    # Where the settings came from, for `noah doctor`. Not settable from config.toml.
    config_path: Path = Field(exclude=True)
    config_found: bool = Field(default=False, exclude=True)


class ConfigError(Exception):
    """config.toml exists but cannot be read or has invalid values."""


def default_config_dir(env: Mapping[str, str | None] | None = None) -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config", os.environ if env is None else env)


def load_settings(
    config_path: Path | None = None,
    env: Mapping[str, str | None] | None = None,
    project_dir: Path | None = None,
) -> Settings:
    """Load settings. `env` and `project_dir` default to the real environment and cwd."""
    if env is None:
        config_dir = default_config_dir()
        project_env = dotenv_values((project_dir or Path.cwd()) / ".env")
        env = {**dotenv_values(config_dir / ".env"), **project_env, **os.environ}
    else:
        config_dir = default_config_dir(env)

    if config_path is None:
        config_path = Path(env.get("NOAH_CONFIG") or config_dir / "config.toml")

    data: dict[str, object] = {}
    if config_path.is_file():
        try:
            data = tomllib.loads(config_path.read_text())
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ConfigError(f"{config_path}: {e}") from e

    data.setdefault("data_dir", _xdg_dir("XDG_DATA_HOME", ".local/share", env))
    data.setdefault("state_dir", _xdg_dir("XDG_STATE_HOME", ".local/state", env))
    if api_key := env.get("OPENROUTER_API_KEY"):
        data["openrouter_api_key"] = api_key

    try:
        return Settings.model_validate(
            {**data, "config_path": config_path, "config_found": config_path.is_file()}
        )
    except ValidationError as e:
        raise ConfigError(f"{config_path}: {e}") from e
