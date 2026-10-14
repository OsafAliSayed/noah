"""Local transcription with faster-whisper. Models download on first use, into
`settings.data_dir / "whisper"`."""

import logging
from pathlib import Path
from typing import Any

from noah.power import SpeechProfile
from noah.tts.base import Samples

log = logging.getLogger(__name__)

# Nudges Whisper towards the words Noah hears most, like its own name.
PROMPT = "Noah, open Firefox. Set the volume to 30. Make a folder in codes."


class WhisperSTT:
    def __init__(
        self, profile: SpeechProfile, download_root: Path | None = None, cpu_threads: int = 6
    ) -> None:
        self.profile = profile
        self.download_root = download_root
        self.cpu_threads = cpu_threads
        self._model: Any = None

    def load(self) -> None:
        if self._model is not None:
            return
        from faster_whisper import WhisperModel

        root = str(self.download_root) if self.download_root else None
        p = self.profile
        log.info("loading Whisper %s on %s (%s)", p.model, p.device, p.compute_type)
        self._model = WhisperModel(
            p.model,
            device=p.device,
            compute_type=p.compute_type,
            cpu_threads=self.cpu_threads,
            download_root=root,
        )

    def transcribe(self, audio: Samples) -> str:
        self.load()
        segments, _ = self._model.transcribe(
            audio,
            language="en",
            beam_size=self.profile.beam_size,
            condition_on_previous_text=False,
            without_timestamps=True,
            initial_prompt=PROMPT,
            vad_filter=False,
        )
        return " ".join(s.text.strip() for s in segments).strip()
