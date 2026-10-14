"""Silero VAD, run frame by frame, and the end-of-speech logic built on it.

The ONNX model ships with faster-whisper, so this needs no torch. Each call takes one
512-sample frame at 16 kHz and keeps the model's state between frames.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np

from noah.audio.input import FRAME, SAMPLE_RATE, Frame

CONTEXT = 64


class SpeechProbability(Protocol):
    def __call__(self, frame: Frame) -> float: ...

    def reset(self) -> None: ...


def _model_path() -> Path:
    import faster_whisper

    return Path(faster_whisper.__file__).parent / "assets" / "silero_vad_v6.onnx"


class SileroVAD:
    def __init__(self, path: Path | None = None) -> None:
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.log_severity_level = 4
        self._session: Any = onnxruntime.InferenceSession(
            str(path or _model_path()), providers=["CPUExecutionProvider"], sess_options=options
        )
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(CONTEXT, dtype=np.float32)

    def __call__(self, frame: Frame) -> float:
        if len(frame) != FRAME:
            raise ValueError(f"VAD frames must be {FRAME} samples")
        audio = np.concatenate([self._context, frame])[None, :]
        prob, self._h, self._c = self._session.run(
            None, {"input": audio, "h": self._h, "c": self._c}
        )
        self._context = frame[-CONTEXT:]
        return float(np.asarray(prob).reshape(-1)[0])


Event = Literal["speech_start", "speech_end", "timeout"] | None


@dataclass
class Endpointer:
    """Decides when someone started and stopped talking, from per-frame speech probabilities.

    - speech starts after `start_ms` of speech-like frames,
    - it ends after `silence_ms` of quiet once it has started,
    - `timeout` fires if nobody starts talking within `wait_ms`, or a turn runs past `max_ms`.
    """

    threshold: float = 0.5
    start_ms: int = 96
    silence_ms: int = 700
    wait_ms: int = 5000
    max_ms: int = 15000

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.started = False
        self._elapsed = 0
        self._voiced = 0
        self._quiet = 0

    def update(self, probability: float) -> Event:
        step = FRAME * 1000 // SAMPLE_RATE
        self._elapsed += step
        speech = probability >= self.threshold
        if not self.started:
            self._voiced = self._voiced + step if speech else 0
            if self._voiced >= self.start_ms:
                self.started = True
                self._quiet = 0
                return "speech_start"
            return "timeout" if self._elapsed >= self.wait_ms else None
        self._quiet = 0 if speech else self._quiet + step
        if self._quiet >= self.silence_ms:
            return "speech_end"
        return "timeout" if self._elapsed >= self.max_ms else None
