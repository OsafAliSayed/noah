"""Wake word detection with openWakeWord (ONNX). Starts with the pretrained "Hey Jarvis"
model; point `[voice] wakeword_model` at a custom .onnx file for "Hey Noah"."""

import os
from pathlib import Path
from typing import Any

import numpy as np

from noah.audio.input import Frame

CHUNK = 1280  # 80 ms at 16 kHz, what openWakeWord's feature model expects
BUILTIN = {"hey_jarvis", "hey_mycroft", "hey_marvin", "alexa"}


def builtin_model_path(name: str) -> Path:
    import openwakeword

    resources = Path(os.path.dirname(openwakeword.__file__)) / "resources" / "models"
    return resources / f"{name}_v0.1.onnx"


class WakeWord:
    def __init__(self, model: str = "hey_jarvis", threshold: float = 0.5) -> None:
        from openwakeword.model import Model

        path = builtin_model_path(model) if model in BUILTIN else Path(model).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"No wake word model at {path}")
        self.threshold = threshold
        self._model: Any = Model(wakeword_model_paths=[str(path)])
        self._buffer = np.zeros(0, dtype=np.int16)

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.int16)
        self._model.reset()

    def __call__(self, frame: Frame) -> bool:
        """Feed audio; True once the wake word is heard."""
        pcm = (np.clip(frame, -1, 1) * 32767).astype(np.int16)
        self._buffer = np.concatenate([self._buffer, pcm])
        heard = False
        while len(self._buffer) >= CHUNK:
            chunk, self._buffer = self._buffer[:CHUNK], self._buffer[CHUNK:]
            scores = self._model.predict(chunk)
            heard = heard or max(scores.values(), default=0) >= self.threshold
        if heard:
            self.reset()
        return heard
