"""The STT interface: 16 kHz mono float32 audio in, text out."""

from typing import Protocol

from noah.tts.base import Samples


class STT(Protocol):
    def transcribe(self, audio: Samples) -> str:
        """Blocking: run it in a thread."""
        ...
