import asyncio

import numpy as np
import pytest

from noah.agent.llm import TextDelta
from noah.agent.loop import Agent
from noah.audio.input import FRAME, Microphone
from noah.audio.vad import Endpointer
from noah.config import VoiceSettings
from noah.hotkey import PushToTalk
from noah.orchestrator import Orchestrator, parse_yes_no, strip_wake_word
from noah.power import ChargerState, choose_profile, watch_charger
from noah.speech import Speaker

from .fakes import FakeLLM, FakeTools, call, text

QUIET, SPEECH, WAKE = 0.0, 0.6, 0.9


def frame(level: float) -> np.ndarray:
    return np.full(FRAME, level, dtype=np.float32)


class FakeVAD:
    def __call__(self, frame) -> float:
        return 1.0 if frame[0] >= SPEECH else 0.0

    def reset(self) -> None:
        pass


class FakeSTT:
    def __init__(self, *texts: str):
        self.texts = list(texts)
        self.heard: list[int] = []

    def transcribe(self, audio) -> str:
        self.heard.append(len(audio))
        return self.texts.pop(0) if self.texts else ""


class FakeTTS:
    sample_rate = 1000

    def __init__(self):
        self.said: list[str] = []

    def synthesize(self, t: str):
        self.said.append(t)
        return np.zeros(0, dtype=np.float32)


class SilentPlayer:
    sample_rate = 1000

    def __init__(self):
        self.played = 0

    async def play(self, samples) -> bool:
        self.played += 1
        return True

    def stop(self) -> None:
        pass

    def close(self) -> None:
        pass


class Clock:
    now = 100.0

    def __call__(self) -> float:
        return self.now


def _orchestrator(stt: FakeSTT, llm: FakeLLM | None = None, tools: FakeTools | None = None, **kw):
    tts = FakeTTS()
    speaker = Speaker(tts, SilentPlayer())  # type: ignore[arg-type]
    lines: list[str] = []
    clock = Clock()
    orch = Orchestrator(
        VoiceSettings(**kw),
        Microphone(stream_factory=lambda **_: None),
        FakeVAD(),
        stt,
        speaker,
        wake=lambda f: f[0] >= WAKE,
        output=lines.append,
        clock=clock,
    )
    if llm:
        orch.agent = Agent(llm, tools or FakeTools(), system_prompt=lambda: "S")
    return orch, tts, lines, clock


async def _finish_turn(orch: Orchestrator) -> None:
    if orch._turn:
        await asyncio.wait_for(orch._turn, 2)


def _speak(orch: Orchestrator, voiced: int = 5, quiet: int = 25) -> None:
    for _ in range(voiced):
        orch.handle("frame", frame(SPEECH))
    for _ in range(quiet):
        orch.handle("frame", frame(QUIET))


async def test_wake_word_listen_answer_and_speak():
    orch, tts, lines, _ = _orchestrator(
        FakeSTT("Hey Jarvis, what time is it?"), FakeLLM(text("It's noon."))
    )
    orch.handle("frame", frame(QUIET))
    assert orch.state == "idle"
    orch.handle("frame", frame(WAKE))
    assert orch.state == "listening"
    _speak(orch)
    assert orch.state == "busy"
    await _finish_turn(orch)
    assert "you> what time is it?" in lines
    assert "noah> It's noon." in lines
    assert tts.said == ["It's noon."]
    assert orch.state == "idle"


async def test_silence_after_wake_word_goes_back_to_idle_without_whisper():
    stt = FakeSTT("ghost words")
    orch, _, _, _ = _orchestrator(stt, FakeLLM())
    orch.handle("frame", frame(WAKE))
    for _ in range(200):  # longer than the 5 s wait
        orch.handle("frame", frame(QUIET))
    assert orch.state == "idle"
    assert stt.heard == []


async def test_push_to_talk_records_until_release():
    stt = FakeSTT("set volume to 30")
    orch, _, lines, _ = _orchestrator(stt, FakeLLM(text("Done.")), mode="ptt")
    orch.wake = None
    orch.handle("ptt_down", None)
    _speak(orch, voiced=10, quiet=40)  # silence doesn't end a push-to-talk recording
    assert orch.state == "listening"
    orch.handle("ptt_up", None)
    await _finish_turn(orch)
    assert stt.heard == [50 * FRAME]
    assert "you> set volume to 30" in lines


async def test_follow_up_needs_no_wake_word():
    orch, _, lines, clock = _orchestrator(
        FakeSTT("open firefox", "and spotify"), FakeLLM(text("Opened."), text("Done."))
    )
    orch.handle("frame", frame(WAKE))
    _speak(orch)
    await _finish_turn(orch)
    clock.now += 1  # past the echo guard, inside the follow-up window
    orch.handle("frame", frame(SPEECH))
    assert orch.state == "listening"
    _speak(orch)
    await _finish_turn(orch)
    assert "you> and spotify" in lines
    clock.now += 10
    orch.handle("frame", frame(SPEECH))
    assert orch.state == "idle"  # the window closed


async def test_barge_in_cancels_the_turn_and_listens():
    started = asyncio.Event()

    class SlowLLM(FakeLLM):
        async def stream(self, messages, tools):  # type: ignore[override]
            started.set()
            await asyncio.sleep(10)
            yield TextDelta("never")

    orch, _, _, _ = _orchestrator(FakeSTT("tell me a long story"), SlowLLM())
    orch.handle("frame", frame(WAKE))
    _speak(orch)
    await asyncio.wait_for(started.wait(), 1)
    turn = orch._turn
    orch.handle("frame", frame(WAKE))
    assert orch.state == "listening"
    with pytest.raises(asyncio.CancelledError):
        await turn  # type: ignore[misc]
    assert orch.agent and orch.agent.history == []


async def test_spoken_confirmation():
    orch, tts, _, _ = _orchestrator(FakeSTT("yes please"))

    async def answer():
        await asyncio.sleep(0.05)
        _speak(orch)

    task = asyncio.create_task(answer())
    assert await asyncio.wait_for(orch.confirm("run `mkdir x`"), 2)
    await task
    assert tts.said == ["I need your OK to run mkdir x.", "Go ahead?"]


async def test_unclear_confirmation_asks_again_then_declines():
    orch, tts, _, _ = _orchestrator(FakeSTT("banana", "hmm"))

    async def answer():
        previous = None
        for _ in range(2):
            while orch._confirm is None or orch._confirm is previous:
                await asyncio.sleep(0.005)
            previous = orch._confirm
            _speak(orch)

    task = asyncio.create_task(answer())
    assert not await asyncio.wait_for(orch.confirm("delete x"), 2)
    await task
    assert "Sorry, was that a yes or a no?" in tts.said
    assert tts.said[-1] == "I'll take that as a no."


async def test_announcements_wait_while_the_user_talks():
    orch, tts, _, _ = _orchestrator(FakeSTT())
    orch.handle("frame", frame(WAKE))
    orch.announce("Two of three done.")
    assert tts.said == []
    for _ in range(200):
        orch.handle("frame", frame(QUIET))
    await orch.speaker.finish()
    assert tts.said == ["Two of three done."]
    orch.do_not_disturb = True
    orch.announce("All done.")
    await orch.speaker.finish()
    assert tts.said == ["Two of three done."]


async def test_confirm_inside_a_checklist_turn():
    """A tool that needs confirmation during a voice turn asks out loud."""
    from noah.tools.registry import ToolRegistry, tool

    @tool(risk="confirm", describe=lambda a: "make the folder")
    def mkdir() -> str:
        """Make it."""
        return "made"

    orch, tts, lines, _ = _orchestrator(FakeSTT("make a folder", "yes"))
    orch.agent = Agent(
        FakeLLM([call("mkdir")], text("Made it.")),
        ToolRegistry([mkdir], confirm=orch.confirm),
        system_prompt=lambda: "S",
    )
    orch.handle("frame", frame(WAKE))
    _speak(orch)
    while orch._confirm is None:
        await asyncio.sleep(0.01)
    _speak(orch)
    await _finish_turn(orch)
    assert "noah> Made it." in lines
    assert "I need your OK to make the folder." in tts.said


def test_yes_no_and_wake_word_stripping():
    assert parse_yes_no("Yes, go ahead.") is True
    assert parse_yes_no("no don't") is False
    assert parse_yes_no("yes no") is None
    assert parse_yes_no("purple") is None
    assert strip_wake_word("Hey Jarvis, open Firefox") == "open Firefox"
    assert strip_wake_word("Noah set the volume") == "set the volume"
    assert strip_wake_word("open Firefox") == "open Firefox"


def test_endpointer():
    e = Endpointer(silence_ms=96, wait_ms=320, max_ms=1000)
    assert [e.update(0.1) for _ in range(9)][-1] is None
    assert e.update(0.1) == "timeout"
    e.reset()
    assert [e.update(0.9) for _ in range(3)] == [None, None, "speech_start"]
    assert [e.update(0.0) for _ in range(3)] == [None, None, "speech_end"]


def test_power_profiles():
    on, off = ChargerState(True, "ACAD"), ChargerState(False, "ACAD")
    assert choose_profile("auto", "large-v3-turbo", "small.en", on, lambda: True).device == "cuda"
    assert (
        choose_profile("auto", "large-v3-turbo", "small.en", off, lambda: True).model == "small.en"
    )
    assert choose_profile("auto", "large-v3-turbo", "small.en", on, lambda: False).device == "cpu"
    assert choose_profile("gpu", "g", "c", None, lambda: True).model == "g"
    assert choose_profile("cpu", "g", "c", on, lambda: True).model == "c"


async def test_watch_charger_reports_changes():
    states = iter([ChargerState(True, "A"), ChargerState(True, "A"), ChargerState(False, "A")])
    seen = []
    task = asyncio.create_task(watch_charger(seen.append, interval=0, read=lambda: next(states)))
    while not seen:
        await asyncio.sleep(0)
    task.cancel()
    assert seen == [ChargerState(False, "A")]


async def test_push_to_talk_ignores_autorepeat():
    events: list[str] = []
    ptt = PushToTalk("ctrl_r", lambda: events.append("down"), lambda: events.append("up"))
    ptt._loop = asyncio.get_running_loop()
    ptt.key_down()
    for _ in range(3):  # X11 auto-repeat: release + press pairs while held
        ptt.key_up()
        ptt.key_down()
    assert events == ["down"]
    ptt.key_up()
    await asyncio.sleep(0.1)
    assert events == ["down", "up"]


async def test_microphone_fans_out_and_drops_old_frames():
    mic = Microphone(stream_factory=lambda **_: None, queue_frames=2)
    a, b = mic.subscribe(), mic.subscribe()
    for level in (0.1, 0.2, 0.3):
        mic.publish([frame(level)])
    assert [round(float(a.get_nowait()[0]), 1) for _ in range(2)] == [0.2, 0.3]
    assert b.qsize() == 2
    mic.unsubscribe(b)
    mic.publish([frame(0.4)])
    assert b.qsize() == 2
