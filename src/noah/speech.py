"""Speaking streamed text: split it into sentences and speak each one as soon as it's complete.

Synthesis of the next sentence overlaps playback of the current one. `stop()` silences
everything at once, for barge-in and "cancel that".
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from noah.audio.player import Player
from noah.tts.base import TTS, Samples

log = logging.getLogger(__name__)

_ABBREVIATIONS = {"e.g", "i.e", "mr", "mrs", "ms", "dr", "vs", "approx"}
_BOUNDARY = re.compile(r"([.!?]+)[\"')\]]*(\s+)|\n+")


class SentenceSplitter:
    """Collects streamed text and hands back whole sentences as they complete."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, text: str) -> list[str]:
        self.buffer += text
        sentences: list[str] = []
        start = 0
        for match in _BOUNDARY.finditer(self.buffer):
            end = match.end()
            candidate = self.buffer[start:end].strip()
            if match.group(1) == ".":
                last_word = candidate.rstrip(".!?\"')]").rsplit(None, 1)[-1:] or [""]
                if last_word[0].lower().rstrip(".") in _ABBREVIATIONS:
                    continue
            if candidate:
                sentences.append(candidate)
            start = end
        self.buffer = self.buffer[start:]
        return sentences

    def flush(self) -> str | None:
        rest, self.buffer = self.buffer.strip(), ""
        return rest or None


def clean_for_speech(text: str) -> str:
    """Strip what reads badly aloud: markdown, code ticks, the home folder's tilde."""
    text = re.sub(r"`+", "", text)
    text = re.sub(r"\*\*|__|(?<!\w)\*(?!\s)|(?<!\s)\*(?!\w)", "", text)
    text = re.sub(r"^\s*(#+|[-*•]|\d+\.)\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"~/", "home/", text)
    text = text.replace("\u2014", ", ").replace("\u2013", ", ")  # em and en dashes
    return " ".join(text.split())


@dataclass
class SpeechTiming:
    """When the first sentence of an utterance was ready, and when its audio started."""

    first_sentence: float | None = None
    first_audio: float | None = None

    @property
    def sentence_to_audio(self) -> float | None:
        if self.first_sentence is None or self.first_audio is None:
            return None
        return self.first_audio - self.first_sentence


class Speaker:
    def __init__(
        self, tts: TTS, player: Player, clock: Callable[[], float] = time.perf_counter
    ) -> None:
        self.tts = tts
        self.player = player
        self.clock = clock
        self.splitter = SentenceSplitter()
        self.timing = SpeechTiming()
        self._generation = 0
        self._sentences: asyncio.Queue[tuple[int, str]] = asyncio.Queue()
        self._audio: asyncio.Queue[tuple[int, Samples]] = asyncio.Queue(maxsize=2)
        self._pending = 0
        self._idle = asyncio.Event()
        self._idle.set()
        self._tasks: list[asyncio.Task[None]] = []

    def start(self) -> None:
        if not self._tasks:
            self._tasks = [
                asyncio.create_task(self._synthesize_loop()),
                asyncio.create_task(self._play_loop()),
            ]

    @property
    def speaking(self) -> bool:
        return not self._idle.is_set()

    def feed(self, text: str) -> None:
        """Streamed reply text: complete sentences are spoken right away."""
        for sentence in self.splitter.feed(text):
            self._enqueue(sentence)

    def say(self, text: str) -> None:
        """A whole line, e.g. a progress announcement, spoken after anything already queued."""
        splitter = SentenceSplitter()
        for sentence in splitter.feed(text):
            self._enqueue(sentence)
        if rest := splitter.flush():
            self._enqueue(rest)

    async def finish(self) -> None:
        """Speak whatever is left of the streamed text, and wait until it has all been heard."""
        if rest := self.splitter.flush():
            self._enqueue(rest)
        await self._idle.wait()

    def stop(self) -> None:
        """Silence now: drop queued sentences and audio, and cut off what's playing."""
        self._generation += 1
        self.splitter.flush()
        for queue in (self._sentences, self._audio):
            while not queue.empty():
                queue.get_nowait()
        self.player.stop()
        self._pending = 0
        self._idle.set()

    async def close(self) -> None:
        self.stop()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        self.player.close()

    def _enqueue(self, sentence: str) -> None:
        sentence = clean_for_speech(sentence)
        if not sentence:
            return
        self.start()
        if self.timing.first_sentence is None:
            self.timing.first_sentence = self.clock()
        self._pending += 1
        self._idle.clear()
        self._sentences.put_nowait((self._generation, sentence))

    def _done_one(self, generation: int) -> None:
        if generation != self._generation:
            return
        self._pending -= 1
        if self._pending <= 0:
            self._pending = 0
            self._idle.set()

    async def _synthesize_loop(self) -> None:
        while True:
            generation, sentence = await self._sentences.get()
            if generation != self._generation:
                continue
            try:
                samples = await asyncio.to_thread(self.tts.synthesize, sentence)
            except Exception:
                log.exception("speech synthesis failed for %r", sentence)
                self._done_one(generation)
                continue
            if generation == self._generation:
                await self._audio.put((generation, samples))

    async def _play_loop(self) -> None:
        while True:
            generation, samples = await self._audio.get()
            if generation != self._generation:
                continue
            if self.timing.first_audio is None:
                self.timing.first_audio = self.clock()
            await self.player.play(samples)
            self._done_one(generation)

    def reset_timing(self) -> SpeechTiming:
        timing, self.timing = self.timing, SpeechTiming()
        return timing
