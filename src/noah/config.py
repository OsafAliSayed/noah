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
    # Provisional pick from a partial M4 benchmark (see docs/model-eval.md): every single-step
    # command right and the fastest first token. Re-run scripts/eval_models.py to confirm.
    model: str = "anthropic/claude-haiku-5.5"
    # Tried in order by OpenRouter if the main model is down or rate limited.
    fallback_models: list[str] = ["deepseek/deepseek-v4.1-flash"]
    # "minimal", "low", "medium" or "high" for models that reason; unset uses the model's default.
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None
    # Don't let OpenRouter's providers store or train on prompts.
    deny_data_collection: bool = True
    # Spoken replies are short. A cap also stops OpenRouter reserving credit for 65k tokens.
    max_tokens: int = Field(default=2048, ge=64)
    max_steps: int = Field(default=8, ge=1)
    # A checklist needs two update_item calls per step on top of the work itself.
    max_steps_with_checklist: int = Field(default=40, ge=1)


class TasksSettings(_Section):
    verbosity: Literal["quiet", "normal", "chatty"] = "normal"
    notifications: bool = True


class SpeechSettings(_Section):
    # A Piper voice name; see https://huggingface.co/rhasspy/piper-voices
    voice: str = "en_US-lessac-medium"
    speed: float = Field(default=1.0, gt=0.3, lt=3.0)


class VoiceSettings(_Section):
    # wake: say the wake word. ptt: hold ptt_key to talk. The hotkey also works in wake mode.
    mode: Literal["wake", "ptt"] = "wake"
    ptt_key: str = "ctrl_r"
    # A built-in openWakeWord model (hey_jarvis, hey_mycroft, alexa) or a path to a .onnx file.
    wakeword_model: str = "hey_jarvis"
    wakeword_threshold: float = Field(default=0.5, gt=0, lt=1)
    mic_device: str | None = None
    vad_threshold: float = Field(default=0.5, gt=0, lt=1)
    silence_ms: int = Field(default=700, ge=200)
    # After Noah answers, listen this long for a follow-up without the wake word. 0 turns it off.
    follow_up_seconds: float = Field(default=5.0, ge=0)
    # Forget the conversation after this much quiet. Checklists are kept.
    session_timeout_seconds: int = Field(default=120, ge=10)
    chime: bool = True
    # The wake word or hotkey while Noah is talking stops it and listens.
    barge_in: bool = True


class STTSettings(_Section):
    # auto: GPU model on the charger, CPU model on battery.
    profile: Literal["auto", "gpu", "cpu"] = "auto"
    # Measured on the GTX 1650 (no tensor cores): small.en takes about 0.47 s per command;
    # large-v3-turbo takes 1.8 s or more. On the CPU, base.en takes about 0.55 s, small.en 1.5 s.
    gpu_model: str = "small.en"
    cpu_model: str = "base.en"
    cpu_threads: int = Field(default=6, ge=1)


class Settings(_Section):
    llm: LLMSettings = LLMSettings()
    tasks: TasksSettings = TasksSettings()
    speech: SpeechSettings = SpeechSettings()
    voice: VoiceSettings = VoiceSettings()
    stt: STTSettings = STTSettings()
    data_dir: Path
    state_dir: Path
    openrouter_api_key: SecretStr | None = None
    # Where the settings came from, for `noah doctor`. Not settable from config.toml.
    config_path: Path = Field(exclude=True)
    config_found: bool = Field(default=False, exclude=True)


def example_config() -> str:
    """The commented config.toml that `noah config init` writes."""
    from importlib.resources import files

    return files("noah").joinpath("config.example.toml").read_text()


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
