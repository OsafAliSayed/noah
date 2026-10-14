"""Wake word detection with openWakeWord (ONNX). Starts with the pretrained "Hey Jarvis"
model; point `[voice] wakeword_model` at a custom .onnx file for "Hey Noah".

The model costs about 3.5% of a core. `SilenceGate` skips it while the room is quiet and,
when sound returns, replays the last two seconds so the start of a wake word isn't lost.
"""

import collections
import logging
import math
import os
from pathlib import Path
from typing import Any

import numpy as np

from noah.audio.input import Frame

log = logging.getLogger(__name__)

CHUNK = 1280  # 80 ms at 16 kHz, what openWakeWord's feature model expects
BUILTIN = {"hey_jarvis", "hey_mycroft", "hey_marvin", "alexa"}


def builtin_model_path(name: str) -> Path:
    import openwakeword

    resources = Path(os.path.dirname(openwakeword.__file__)) / "resources" / "models"
    return resources / f"{name}_v0.1.onnx"


def chunk_dbfs(chunk: np.ndarray) -> float:
    rms = math.sqrt(float(np.mean(chunk.astype(np.float32) ** 2)))
    return 20 * math.log10(rms / 32768 + 1e-9)


class SilenceGate:
    """Decides which 80 ms chunks the wake word model needs to see.

    After `quiet_chunks` chunks below `threshold_dbfs` the gate closes and only buffers the
    most recent `preroll_chunks`. The first loud chunk opens it, and the buffer is handed back
    so the model catches up on the audio leading into the sound.
    """

    def __init__(
        self, threshold_dbfs: float, quiet_chunks: int = 25, preroll_chunks: int = 25
    ) -> None:
        self.threshold_dbfs = threshold_dbfs
        self.quiet_chunks = quiet_chunks
        self.closed = False
        self._quiet_run = 0
        self._preroll: collections.deque[np.ndarray] = collections.deque(maxlen=preroll_chunks)

    def __call__(self, chunk: np.ndarray) -> list[np.ndarray]:
        level = chunk_dbfs(chunk)
        loud = level >= self.threshold_dbfs
        self._quiet_run = 0 if loud else self._quiet_run + 1
        if not self.closed:
            if self._quiet_run >= self.quiet_chunks:
                self.closed = True
                self._preroll.clear()
                log.debug("wake word model resting: mic at %.0f dBFS", level)
            return [chunk]
        self._preroll.append(chunk)
        if not loud:
            return []
        self.closed = False
        log.debug("wake word model waking: mic at %.0f dBFS", level)
        chunks = list(self._preroll)
        self._preroll.clear()
        return chunks

    def reset(self) -> None:
        self.closed = False
        self._quiet_run = 0
        self._preroll.clear()


class WakeWord:
    def __init__(
        self,
        model: str = "hey_jarvis",
        threshold: float = 0.5,
        gate_dbfs: float | None = -50,
    ) -> None:
        from openwakeword.model import Model

        path = builtin_model_path(model) if model in BUILTIN else Path(model).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"No wake word model at {path}")
        self.threshold = threshold
        self._model: Any = Model(wakeword_model_paths=[str(path)])
        self._buffer = np.zeros(0, dtype=np.int16)
        self.gate = SilenceGate(gate_dbfs) if gate_dbfs is not None else None

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.int16)
        self._model.reset()
        if self.gate:
            self.gate.reset()

    def __call__(self, frame: Frame) -> bool:
        """Feed audio; True once the wake word is heard."""
        pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16)
        self._buffer = np.concatenate([self._buffer, pcm])
        heard = False
        while len(self._buffer) >= CHUNK:
            chunk, self._buffer = self._buffer[:CHUNK], self._buffer[CHUNK:]
            for run in self.gate(chunk) if self.gate else [chunk]:
                scores = self._model.predict(run)
                heard = heard or max(scores.values(), default=0) >= self.threshold
        if heard:
            self.reset()
        return heard
