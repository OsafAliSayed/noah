"""Piper TTS: local, CPU-only, much faster than real time on the target laptop.

Voices are downloaded from Hugging Face (rhasspy/piper-voices) on first use, into
`settings.data_dir / "voices"`.
"""

import logging
from pathlib import Path
from typing import Any

import numpy as np

from noah.tts.base import Samples

log = logging.getLogger(__name__)

DEFAULT_VOICE = "en_US-lessac-medium"


class VoiceDownloadError(Exception):
    pass


def voice_path(voice: str, voices_dir: Path) -> Path:
    return voices_dir / f"{voice}.onnx"


def ensure_voice(voice: str, voices_dir: Path) -> Path:
    """Return the voice's .onnx file, downloading it (about 60 MB) if it isn't there yet."""
    model = voice_path(voice, voices_dir)
    if model.is_file() and model.with_suffix(".onnx.json").is_file():
        return model
    from piper.download_voices import download_voice

    log.info("downloading Piper voice %s to %s", voice, voices_dir)
    voices_dir.mkdir(parents=True, exist_ok=True)
    try:
        download_voice(voice, voices_dir)
    except Exception as e:
        raise VoiceDownloadError(f"Couldn't download the Piper voice {voice}: {e}") from e
    return model


class PiperTTS:
    def __init__(
        self, voice: str = DEFAULT_VOICE, voices_dir: Path | None = None, speed: float = 1.0
    ) -> None:
        self.voice = voice
        self.voices_dir = voices_dir or Path.home() / ".local/share/noah/voices"
        self.speed = speed
        self._voice: Any = None

    def load(self) -> None:
        """Load the voice now, so the first sentence isn't slowed by it."""
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(ensure_voice(self.voice, self.voices_dir))

    @property
    def sample_rate(self) -> int:
        self.load()
        return self._voice.config.sample_rate

    def synthesize(self, text: str) -> Samples:
        self.load()
        from piper import SynthesisConfig

        # Piper's length_scale is duration: smaller is faster.
        config = SynthesisConfig(length_scale=1.0 / self.speed)
        chunks = [c.audio_float_array for c in self._voice.synthesize(text, config)]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False)
