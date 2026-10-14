"""Charger state, read from /sys. M6 builds the STT power profiles on top of this."""

from dataclasses import dataclass
from pathlib import Path

POWER_SUPPLY_DIR = Path("/sys/class/power_supply")


@dataclass(frozen=True)
class ChargerState:
    online: bool
    supply: str  # e.g. "ACAD"


def read_charger(power_dir: Path = POWER_SUPPLY_DIR) -> ChargerState | None:
    """Return the first mains supply's state, or None if the machine reports no charger."""
    for supply in sorted(power_dir.glob("*")):
        try:
            if (supply / "type").read_text().strip() != "Mains":
                continue
            online = (supply / "online").read_text().strip() == "1"
        except OSError:
            continue
        return ChargerState(online=online, supply=supply.name)
    return None
