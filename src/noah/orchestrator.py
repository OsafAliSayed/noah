"""The voice loop: wake word or hotkey → listen → transcribe → agent turn → speak.

One coroutine owns all audio. Microphone frames and hotkey presses arrive as events and are
handled according to the current state, so the wake word, end-of-speech detection, barge-in
and spoken confirmations never compete for frames.

    IDLE ──wake word / hotkey──► LISTENING ──silence / release──► BUSY (transcribe, think,
      ▲                              ▲                                 act, speak)
      │                              └──── barge-in / follow-up ────────┤
      └──────────────────────────── turn finished ──────────────────────┘
"""

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from noah.agent.llm import LLMError, TextDelta
from noah.agent.loop import Agent
from noah.audio import chime
from noah.audio.input import FRAME, SAMPLE_RATE, Frame, Microphone
from noah.audio.vad import Endpointer, SpeechProbability
from noah.config import VoiceSettings
from noah.metrics import TurnTimer
from noah.speech import Speaker
from noah.stt.base import STT
from noah.tts.base import Samples

log = logging.getLogger(__name__)

State = Literal["idle", "listening", "busy"]
Trigger = Literal["wake", "ptt", "follow_up"]
Detector = Callable[[Frame], bool]
Output = Callable[[str], None]

MAX_PTT_SECONDS = 30
ECHO_GUARD_SECONDS = 0.3  # ignore the tail of Noah's own voice before the follow-up window
CONFIRM_SECONDS = 8

_WAKE_PREFIX = re.compile(r"^\W*(hey|hi|ok|okay)?\W*(jarvis|noah|mycroft)\b\W*", re.IGNORECASE)
_YES = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|go ahead|do it|confirm|affirmative|please do|correct)\b"
)
_NO = re.compile(r"\b(no|nope|nah|don't|do not|stop|cancel|negative|wait)\b")


def strip_wake_word(text: str) -> str:
    return _WAKE_PREFIX.sub("", text).strip()


def parse_yes_no(text: str) -> bool | None:
    """True for yes, False for no, None if it's unclear (or both)."""
    lowered = text.lower()
    yes, no = bool(_YES.search(lowered)), bool(_NO.search(lowered))
    if yes == no:
        return None
    return yes


@dataclass
class _Recording:
    trigger: Trigger
    endpointer: Endpointer | None  # None: the hotkey decides when it ends
    frames: list[Frame] = field(default_factory=list)
    speech_frames: int = 0
    ptt_held: bool = False

    def audio(self) -> Samples:
        if not self.frames:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self.frames).astype(np.float32)


@dataclass
class _ConfirmRequest:
    future: asyncio.Future[_Recording | None]
    recording: _Recording


class Orchestrator:
    def __init__(
        self,
        settings: VoiceSettings,
        mic: Microphone,
        vad: SpeechProbability,
        stt: STT,
        speaker: Speaker,
        wake: Detector | None = None,
        output: Output = print,
        timing_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self.mic = mic
        self.vad = vad
        self.stt = stt
        self.speaker = speaker
        self.wake = wake
        self.output = output
        self.timing_path = timing_path
        self.clock = clock
        self.agent: Agent | None = None
        self.on_turn_end: Callable[[], None] | None = None

        self.state: State = "idle"
        self.do_not_disturb = False
        self._events: asyncio.Queue[tuple[str, Frame | None]] = asyncio.Queue(maxsize=1000)
        self._recording: _Recording | None = None
        self._confirm: _ConfirmRequest | None = None
        self._turn: asyncio.Task[None] | None = None
        self._held: list[str] = []
        self._follow_up_from = self._follow_up_until = 0.0
        self._last_activity = clock()
        self._preroll: deque[Frame] = deque(maxlen=10)  # about 320 ms

    # --- inputs ------------------------------------------------------------------------

    def ptt_down(self) -> None:
        self._put("ptt_down")

    def ptt_up(self) -> None:
        self._put("ptt_up")

    def _put(self, kind: str, frame: Frame | None = None) -> None:
        if self._events.full():
            self._events.get_nowait()
        self._events.put_nowait((kind, frame))

    async def _pump(self) -> None:
        frames = self.mic.subscribe()
        try:
            while True:
                self._put("frame", await frames.get())
        finally:
            self.mic.unsubscribe(frames)

    async def run(self) -> None:
        pump = asyncio.create_task(self._pump())
        try:
            while True:
                kind, frame = await self._events.get()
                self.handle(kind, frame)
        finally:
            pump.cancel()
            if self._turn:
                self._turn.cancel()
            await asyncio.gather(pump, *(t for t in [self._turn] if t), return_exceptions=True)

    # --- outputs the session wires up ------------------------------------------------------

    def announce(self, line: str) -> None:
        """Progress lines from the announcer: printed, and spoken unless that would talk over
        the user or do-not-disturb is on."""
        self.output(f"  ● {line}")
        if self.do_not_disturb:
            return
        if self.state == "listening" or self._confirm is not None:
            self._held.append(line)
        else:
            self.speaker.say(line)

    async def confirm(self, action: str) -> bool:
        """Spoken confirmation: ask, listen for yes or no, ask once more if it's unclear."""
        question = f"I need your OK to {action}. Go ahead?"
        for _ in range(2):
            self.output(f"  ? {question}")
            self.speaker.say(question)
            await self.speaker.finish()
            recording = await self._listen_for_answer()
            text = await self._transcribe(recording) if recording else ""
            self.output(f"  you: {text or '(nothing heard)'}")
            answer = parse_yes_no(text)
            if answer is not None:
                return answer
            question = "Sorry, was that a yes or a no?"
        self.speaker.say("I'll take that as a no.")
        await self.speaker.finish()
        return False

    async def _listen_for_answer(self) -> _Recording | None:
        loop = asyncio.get_running_loop()
        endpointer = self._endpointer(wait_ms=CONFIRM_SECONDS * 1000, max_ms=10_000)
        request = _ConfirmRequest(loop.create_future(), _Recording("follow_up", endpointer))
        self.vad.reset()
        self._confirm = request
        self._chime(chime.listening)
        try:
            return await request.future
        finally:
            self._confirm = None
            self._flush_held()

    # --- the state machine -----------------------------------------------------------------

    def handle(self, kind: str, frame: Frame | None) -> None:
        if self._confirm is not None:
            self._handle_confirm(kind, frame)
        elif self.state == "idle":
            self._handle_idle(kind, frame)
        elif self.state == "listening":
            self._handle_listening(kind, frame)
        else:
            self._handle_busy(kind, frame)

    def _handle_idle(self, kind: str, frame: Frame | None) -> None:
        if kind == "ptt_down":
            self._start_listening("ptt")
            return
        if kind != "frame" or frame is None:
            return
        if self.wake and self.wake(frame):
            self._start_listening("wake")
            return
        if self._follow_up_from <= self.clock() < self._follow_up_until:
            self._preroll.append(frame)
            if self.vad(frame) >= self.settings.vad_threshold:
                self._start_listening("follow_up", preroll=True)
        elif self.clock() - self._last_activity > self.settings.session_timeout_seconds:
            if self.agent and self.agent.history:
                log.info("session timed out; clearing the conversation")
                self.agent.reset()

    def _start_listening(self, trigger: Trigger, preroll: bool = False) -> None:
        self.speaker.stop()
        self.state = "listening"
        self._follow_up_until = 0
        self.vad.reset()
        endpointer = None if trigger == "ptt" else self._endpointer()
        self._recording = _Recording(trigger, endpointer, ptt_held=trigger == "ptt")
        if preroll:
            self._recording.frames.extend(self._preroll)
            self._recording.speech_frames = 1
            if endpointer:
                endpointer.started = True
        self._preroll.clear()
        if trigger != "follow_up":
            self._chime(chime.listening)
        self.output("  (listening)")

    def _handle_listening(self, kind: str, frame: Frame | None) -> None:
        recording = self._recording
        assert recording is not None
        if kind == "ptt_up" and recording.trigger == "ptt":
            self._finish_listening()
            return
        if kind != "frame" or frame is None:
            return
        recording.frames.append(frame)
        probability = self.vad(frame)
        if probability >= self.settings.vad_threshold:
            recording.speech_frames += 1
        if recording.endpointer is None:
            if len(recording.frames) * FRAME > MAX_PTT_SECONDS * SAMPLE_RATE:
                self._finish_listening()
            return
        event = recording.endpointer.update(probability)
        if event == "speech_end":
            self._finish_listening()
        elif event == "timeout":
            if recording.endpointer.started:
                self._finish_listening()
            else:
                self._back_to_idle(follow_up=False)

    def _finish_listening(self) -> None:
        recording = self._recording
        self._recording = None
        self.state = "busy"
        if recording is None or recording.speech_frames == 0:
            # Nobody spoke: don't send Whisper silence, it invents words.
            self._back_to_idle(follow_up=False)
            return
        self._chime(chime.done_listening)
        self._turn = asyncio.create_task(self._run_turn(recording))

    def _handle_busy(self, kind: str, frame: Frame | None) -> None:
        if not self.settings.barge_in:
            return
        if kind == "ptt_down":
            self._barge_in("ptt")
        elif kind == "frame" and frame is not None and self.wake and self.wake(frame):
            self._barge_in("wake")

    def _barge_in(self, trigger: Trigger) -> None:
        log.info("barge-in")
        self.speaker.stop()
        if self._turn and not self._turn.done():
            self._turn.cancel()
        self._turn = None
        self._start_listening(trigger)

    def _handle_confirm(self, kind: str, frame: Frame | None) -> None:
        request = self._confirm
        assert request is not None
        recording = request.recording
        if kind == "ptt_down":
            recording.ptt_held = True
            recording.endpointer = None
            return
        if kind == "ptt_up" and recording.ptt_held:
            self._resolve_confirm(recording)
            return
        if kind != "frame" or frame is None:
            return
        recording.frames.append(frame)
        probability = self.vad(frame)
        if probability >= self.settings.vad_threshold:
            recording.speech_frames += 1
        if recording.endpointer is None:
            return
        event = recording.endpointer.update(probability)
        if event == "speech_end" or (event == "timeout" and recording.endpointer.started):
            self._resolve_confirm(recording)
        elif event == "timeout":
            self._resolve_confirm(None)

    def _resolve_confirm(self, recording: _Recording | None) -> None:
        request = self._confirm
        if request and not request.future.done():
            if recording is not None and recording.speech_frames == 0:
                recording = None
            request.future.set_result(recording)

    def _back_to_idle(self, follow_up: bool) -> None:
        self.state = "idle"
        self._last_activity = self.clock()
        now = self.clock()
        wants_follow_up = follow_up and self.wake is not None and self.settings.follow_up_seconds
        self._follow_up_from = now + ECHO_GUARD_SECONDS
        self._follow_up_until = now + self.settings.follow_up_seconds if wants_follow_up else 0
        self.vad.reset()
        self._flush_held()

    def _flush_held(self) -> None:
        held, self._held = self._held, []
        if not self.do_not_disturb:
            for line in held:
                self.speaker.say(line)

    # --- a turn ------------------------------------------------------------------------

    async def _transcribe(self, recording: _Recording) -> str:
        try:
            return await asyncio.to_thread(self.stt.transcribe, recording.audio())
        except Exception:
            log.exception("transcription failed")
            return ""

    async def _run_turn(self, recording: _Recording) -> None:
        timer = TurnTimer(self.timing_path)
        self.speaker.reset_timing()
        follow_up = True
        try:
            text = strip_wake_word(await self._transcribe(recording))
            timer.mark("transcribed")
            if not text:
                follow_up = False
                return
            self.output(f"you> {text}")
            if self.agent is None:
                return
            reply: list[str] = []
            try:
                async for event in self.agent.run(text):
                    if isinstance(event, TextDelta):
                        timer.mark("llm_first_token")
                        reply.append(event.text)
                        self.speaker.feed(event.text)
            except LLMError as e:
                self.output(f"  ✗ {e}")
                self.speaker.say(str(e))
            if reply:
                self.output("noah> " + "".join(reply).strip())
            if self.on_turn_end:
                self.on_turn_end()
            await self.speaker.finish()
            if self.speaker.timing.first_audio is not None:
                timer.mark("first_audio", self.speaker.timing.first_audio)
            timer.record(mode="voice", trigger=recording.trigger, text=text)
        except asyncio.CancelledError:
            follow_up = False
            raise
        finally:
            if self._turn is asyncio.current_task():
                self._turn = None
                self._back_to_idle(follow_up)

    def _endpointer(self, wait_ms: int = 5000, max_ms: int = 15000) -> Endpointer:
        return Endpointer(
            threshold=self.settings.vad_threshold,
            silence_ms=self.settings.silence_ms,
            wait_ms=wait_ms,
            max_ms=max_ms,
        )

    def _chime(self, make: Callable[[int], Samples]) -> None:
        if not self.settings.chime:
            return
        player = self.speaker.player
        task = asyncio.ensure_future(player.play(make(player.sample_rate)))
        task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)

    async def close(self) -> None:
        if self._turn:
            self._turn.cancel()
            with contextlib.suppress(BaseException):
                await self._turn
