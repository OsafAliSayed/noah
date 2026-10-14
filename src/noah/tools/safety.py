"""The safety gate: every tool call is assessed as safe, confirm or deny before it runs.

Static risks are declared on the tool. Tools whose risk depends on their arguments (the shell,
file paths) use the classifiers here.
"""

import json
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

Risk = Literal["safe", "confirm", "deny"]
Confirm = Callable[[str], Awaitable[bool]]


class CannotAsk(Exception):
    """Raised by a `Confirm` callback when there's no way to ask the user (e.g. piped input),
    so the action is skipped without being reported as the user saying no."""


@dataclass(frozen=True)
class Assessment:
    risk: Risk
    reason: str | None = None


SAFE = Assessment("safe")
CONFIRM = Assessment("confirm")


# --- secrets -------------------------------------------------------------------------------

_SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".password-store", "keyrings", ".kube"}
_SECRET_NAMES = {".netrc", ".pgpass", ".git-credentials", "credentials.json"}


def is_secret_path(path: Path) -> bool:
    """Files Noah must never read or send to the model: keys, tokens, .env files."""
    if any(part in _SECRET_DIRS for part in path.parts):
        return True
    name = path.name
    return name in _SECRET_NAMES or (
        (name == ".env" or name.startswith(".env.")) and name != ".env.example"
    )


# --- paths ---------------------------------------------------------------------------------


def resolve_user_path(raw: str, home: Path | None = None) -> Path:
    home = home or Path.home()
    path = Path(raw).expanduser()
    return (path if path.is_absolute() else home / path).resolve()


def assess_home_path(raw: str, home: Path | None = None) -> Assessment:
    """File tools are limited to the home folder, and never touch secrets."""
    home = (home or Path.home()).resolve()
    path = resolve_user_path(raw, home)
    if not path.is_relative_to(home):
        return Assessment("deny", f"{path} is outside your home folder.")
    if is_secret_path(path):
        return Assessment("deny", f"{path} may contain secrets, so Noah won't read it.")
    return SAFE


# --- shell ---------------------------------------------------------------------------------

_DENY_PATTERNS: list[tuple[str, str]] = [
    (r"(^|[\s;&|(])(sudo|su|pkexec|doas)(\s|$)", "Noah never runs commands as root."),
    (r"\bmkfs|\bwipefs\b|\bfdisk\b|\bparted\b", "Disk formatting and partitioning is off limits."),
    (r"\bdd\b.*\bof=", "Raw disk writes with dd are off limits."),
    (r">\s*/dev/(sd|nvme|hd|mmcblk)", "Writing to a disk device is off limits."),
    (r"--no-preserve-root", "That would delete the whole system."),
    (
        r"\brm\b(\s+-[-\w]+)*\s+(--\s+)?(/|~|\$HOME|\$\{HOME\})/?\*?(\s|$)",
        "That would delete your whole home folder or system.",
    ),
    (
        r"\b(chmod|chown)\b\s+(-\w*R\w*\s+).*\s(/|~)/?(\s|$)",
        "Recursive permission changes on / or ~ are off limits.",
    ),
    (r">>?\s*/(etc|usr|boot|bin|sbin|lib|lib64|var|sys|proc)\b", "System files are off limits."),
    (
        r"\b(curl|wget)\b.*\|\s*(sudo\s+)?(ba|z|da)?sh\b",
        "Piping a download into a shell is off limits.",
    ),
    (r":\(\)\s*\{.*\};\s*:", "That's a fork bomb."),
]

# Read-only commands that run without asking, when used without any shell operators.
_SAFE_COMMANDS = {
    "ls", "pwd", "cat", "head", "tail", "wc", "which", "whoami", "hostname", "id", "date",
    "uname", "uptime", "df", "du", "free", "lsblk", "file", "stat", "echo",
}  # fmt: skip
_SAFE_GIT = {"status", "log", "diff", "branch", "show", "remote"}
_SHELL_OPERATORS = re.compile(r"[;&|<>`$\n]")


def assess_shell(command: str) -> Assessment:
    for pattern, reason in _DENY_PATTERNS:
        if re.search(pattern, command):
            return Assessment("deny", reason)

    if _SHELL_OPERATORS.search(command):
        return CONFIRM
    try:
        words = shlex.split(command)
    except ValueError:
        return CONFIRM
    if not words:
        return CONFIRM
    if any(is_secret_path(Path(w).expanduser()) for w in words[1:]):
        return CONFIRM
    if words[0] in _SAFE_COMMANDS:
        return SAFE
    if words[0] == "git" and len(words) > 1 and words[1] in _SAFE_GIT:
        return SAFE
    return CONFIRM


# --- audit log -----------------------------------------------------------------------------


class AuditLog:
    """Appends one JSON line per tool call to ~/.local/state/noah/actions.log."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, **fields: Any) -> None:
        entry = {"ts": datetime.now().astimezone().isoformat(timespec="seconds"), **fields}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except OSError:
            pass  # A full disk or read-only home must not stop Noah from answering.
