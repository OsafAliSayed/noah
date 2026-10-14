"""Per-turn timing and memory log, at `settings.state_dir / "timing.log"` (one JSON line per turn).

"Smooth" is checked with numbers: how long each stage took and how much RAM Noah used.
"""

import json
import os
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import psutil


def rss_mb(pid: int | None = None) -> float:
    return psutil.Process(pid or os.getpid()).memory_info().rss / 1e6


class TurnTimer:
    """Marks named moments relative to the start of a turn, then logs them."""

    def __init__(
        self,
        path: Path | None,
        clock: Callable[[], float] = time.perf_counter,
        memory: Callable[[], float] = rss_mb,
    ) -> None:
        self.path = path
        self.clock = clock
        self.memory = memory
        self.start = clock()
        self.marks: dict[str, float] = {}

    def mark(self, name: str, at: float | None = None) -> None:
        """Record a moment once; later marks with the same name are ignored."""
        if name not in self.marks:
            self.marks[name] = round((at if at is not None else self.clock()) - self.start, 3)

    def record(self, **extra: object) -> dict[str, object]:
        entry: dict[str, object] = {
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            **self.marks,
            "total": round(self.clock() - self.start, 3),
            "rss_mb": round(self.memory(), 1),
            **extra,
        }
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a") as f:
                    f.write(json.dumps(entry) + "\n")
            except OSError:
                pass
        return entry
