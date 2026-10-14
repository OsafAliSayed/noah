"""The TTS interface. Engines turn one sentence into mono float32 samples."""

from typing import Protocol

import numpy as np
import numpy.typing as npt

Samples = npt.NDArray[np.float32]


class TTS(Protocol):
    @property
    def sample_rate(self) -> int: ...

    def synthesize(self, text: str) -> Samples:
        """Blocking: run it in a thread. Returns mono samples in [-1, 1]."""
        ...
