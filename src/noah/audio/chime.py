"""Short tones so you know Noah is listening, without words."""

import numpy as np

from noah.tts.base import Samples


def tone(freqs: list[float], sample_rate: int, note_ms: int = 70, volume: float = 0.25) -> Samples:
    parts = []
    for freq in freqs:
        t = np.arange(int(sample_rate * note_ms / 1000)) / sample_rate
        envelope = np.minimum(1, np.minimum(t, t[::-1]) * 200)  # 5 ms fade in and out
        parts.append(np.sin(2 * np.pi * freq * t) * envelope * volume)
    return np.concatenate(parts).astype(np.float32)


def listening(sample_rate: int) -> Samples:
    return tone([660, 990], sample_rate)


def done_listening(sample_rate: int) -> Samples:
    return tone([990, 660], sample_rate, note_ms=50, volume=0.15)
