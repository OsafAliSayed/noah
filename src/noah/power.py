"""Charger state from /sys, and the speech-to-text profile that suits it.

On the charger, Whisper runs on the GPU. On battery it runs a smaller English model on
the CPU, which costs far less power.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

log = logging.getLogger(__name__)

POWER_SUPPLY_DIR = Path("/sys/class/power_supply")

ProfileName = Literal["gpu", "cpu"]


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


@dataclass(frozen=True)
class SpeechProfile:
    name: ProfileName
    model: str
    device: Literal["cuda", "cpu"]
    compute_type: str
    beam_size: int


def cuda_available() -> bool:
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def choose_profile(
    setting: Literal["auto", "gpu", "cpu"],
    gpu_model: str,
    cpu_model: str,
    charger: ChargerState | None,
    has_cuda: Callable[[], bool] = cuda_available,
) -> SpeechProfile:
    # Greedy decoding: beam search costs a lot of time for little gain on short commands.
    gpu = SpeechProfile("gpu", gpu_model, "cuda", "int8_float16", beam_size=1)
    cpu = SpeechProfile("cpu", cpu_model, "cpu", "int8", beam_size=1)
    if setting == "cpu":
        return cpu
    if setting == "gpu":
        return gpu if has_cuda() else cpu
    on_charger = charger is not None and charger.online
    return gpu if on_charger and has_cuda() else cpu


async def watch_charger(
    on_change: Callable[[ChargerState | None], None],
    interval: float = 5.0,
    read: Callable[[], ChargerState | None] = read_charger,
) -> None:
    """Call `on_change` whenever the charger is plugged in or removed. Runs until cancelled."""
    last = read()
    while True:
        await asyncio.sleep(interval)
        current = read()
        if current != last:
            last = current
            on_change(current)


def preload_cuda_libraries() -> int:
    """Load cuBLAS and cuDNN from the pip `nvidia-*` packages (the `gpu` extra), so
    CTranslate2 finds them without LD_LIBRARY_PATH. Returns how many libraries loaded."""
    import ctypes

    try:
        import nvidia  # type: ignore[import-not-found]
    except ImportError:
        return 0
    files = [
        f
        for root in map(Path, nvidia.__path__)
        for pattern in ("cublas/lib/lib*.so.*", "cudnn/lib/lib*.so.*")
        for f in sorted(root.glob(pattern))
    ]
    loaded = 0
    # Some depend on others; keep going round until nothing more loads.
    while files:
        remaining = []
        for f in files:
            try:
                ctypes.CDLL(str(f), mode=ctypes.RTLD_GLOBAL)
                loaded += 1
            except OSError:
                remaining.append(f)
        if len(remaining) == len(files):
            log.debug("couldn't load %s", [f.name for f in remaining])
            break
        files = remaining
    return loaded
