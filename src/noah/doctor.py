"""`noah doctor`: check that this machine has what Noah needs, and say how to fix what's missing.

Each check returns a Check. `fail` means Noah can't work; `warn` means a feature will be
missing or slower. Checks take their system access as arguments so tests can fake it.
"""

import ctypes.util
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import psutil

from noah.config import Settings

Status = Literal["ok", "warn", "fail"]
Runner = Callable[[list[str]], str | None]


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str
    hint: str | None = None


# command -> (apt package, what Noah uses it for)
SYSTEM_COMMANDS: dict[str, tuple[str, str]] = {
    "xdg-open": ("xdg-utils", "opening files and URLs"),
    "gtk-launch": ("libgtk-3-bin", "launching apps"),
    "wpctl": ("wireplumber", "volume control"),
    "notify-send": ("libnotify-bin", "desktop notifications"),
    "playerctl": ("playerctl", "media control"),
    "brightnessctl": ("brightnessctl", "screen brightness"),
    "xclip": ("xclip", "clipboard"),
    "xdotool": ("xdotool", "window control"),
    "wmctrl": ("wmctrl", "window control"),
}

MIN_FREE_RAM_BYTES = 1_500_000_000


def run_command(cmd: list[str]) -> str | None:
    """Run a command and return its stdout, or None if it is missing, fails or hangs."""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout


def check_python() -> Check:
    # requires-python already enforces 3.13+, so this only reports which interpreter is in use.
    return Check("Python", "ok", ".".join(map(str, sys.version_info[:3])))


def check_config(settings: Settings | None, error: str | None) -> Check:
    if settings is None:
        return Check("Config", "fail", error or "could not load", "Fix or remove the config file.")
    if settings.config_found:
        return Check("Config", "ok", str(settings.config_path))
    return Check("Config", "ok", f"using defaults ({settings.config_path} not found)")


def check_api_key(settings: Settings | None) -> Check:
    if settings is not None and settings.openrouter_api_key:
        return Check("OpenRouter API key", "ok", "set")
    return Check(
        "OpenRouter API key",
        "fail",
        "not set",
        "Create one at https://openrouter.ai/keys and add OPENROUTER_API_KEY to .env "
        "or ~/.config/noah/.env.",
    )


def check_commands(which: Callable[[str], str | None] = shutil.which) -> list[Check]:
    checks: list[Check] = []
    for cmd, (package, purpose) in SYSTEM_COMMANDS.items():
        if which(cmd):
            checks.append(Check(cmd, "ok", purpose))
        else:
            checks.append(
                Check(cmd, "warn", f"missing, needed for {purpose}", f"sudo apt install {package}")
            )
    return checks


def check_portaudio(find_library: Callable[[str], str | None] = ctypes.util.find_library) -> Check:
    if find_library("portaudio"):
        return Check("PortAudio", "ok", "found")
    return Check(
        "PortAudio",
        "warn",
        "missing, needed for voice (M5+)",
        "sudo apt install portaudio19-dev",
    )


def check_mic(run: Runner = run_command) -> Check:
    output = run(["pactl", "info"])
    if output is None:
        return Check("Microphone", "warn", "could not query PipeWire/PulseAudio (pactl)")
    for line in output.splitlines():
        if line.startswith("Default Source:"):
            source = line.split(":", 1)[1].strip()
            if source and source != "@DEFAULT_SOURCE@":
                return Check("Microphone", "ok", source)
    return Check("Microphone", "warn", "no default input device", "Pick a mic in Settings > Sound.")


def check_gpu(run: Runner = run_command) -> Check:
    output = run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"])
    if output and output.strip():
        name, memory = (part.strip() for part in output.strip().splitlines()[0].split(",", 1))
        return Check("GPU", "ok", f"{name}, {memory}: speech-to-text can use the GPU on charger")
    return Check("GPU", "warn", "no NVIDIA GPU found: speech-to-text will run on the CPU")


def check_power(power_dir: Path = Path("/sys/class/power_supply")) -> Check:
    for supply in sorted(power_dir.glob("*")):
        try:
            if (supply / "type").read_text().strip() != "Mains":
                continue
            online = (supply / "online").read_text().strip() == "1"
        except OSError:
            continue
        state = "on charger" if online else "on battery"
        return Check("Power", "ok", f"{state} ({supply.name})")
    return Check("Power", "warn", "no charger found: Noah will always use the battery profile")


def check_session(env: Mapping[str, str | None]) -> Check:
    session = env.get("XDG_SESSION_TYPE") or "unknown"
    desktop = env.get("XDG_CURRENT_DESKTOP") or "unknown"
    if session == "x11":
        return Check("Desktop session", "ok", f"{desktop} on X11")
    return Check(
        "Desktop session",
        "warn",
        f"{desktop} on {session}: window and clipboard tools are built for X11",
    )


def check_memory(available: Callable[[], int] = lambda: psutil.virtual_memory().available) -> Check:
    free = available()
    detail = f"{free / 1e9:.1f} GB available"
    if free >= MIN_FREE_RAM_BYTES:
        return Check("Free RAM", "ok", detail)
    return Check("Free RAM", "warn", f"{detail}: Noah needs about 1 GB once voice is on")


def run_checks(
    settings: Settings | None, config_error: str | None, env: Mapping[str, str | None]
) -> list[Check]:
    return [
        check_python(),
        check_config(settings, config_error),
        check_api_key(settings),
        check_session(env),
        check_memory(),
        check_power(),
        check_gpu(),
        check_mic(),
        check_portaudio(),
        *check_commands(),
    ]
