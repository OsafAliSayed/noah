"""`noah run`: the voice daemon. Builds the audio pipeline, then hands control to the
orchestrator until Ctrl-C (or systemd) stops it."""

import asyncio
import contextlib
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass

from noah.audio.input import Microphone
from noah.audio.vad import SileroVAD
from noah.audio.wakeword import WakeWord
from noah.config import Settings
from noah.hotkey import PushToTalk
from noah.orchestrator import Orchestrator
from noah.power import (
    ChargerState,
    SpeechProfile,
    choose_profile,
    preload_cuda_libraries,
    read_charger,
    watch_charger,
)
from noah.session import make_speaker, start
from noah.stt.faster_whisper import WhisperSTT
from noah.tasks.manager import TaskEvent

log = logging.getLogger(__name__)
Output = Callable[[str], None]


def _print(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _profile(settings: Settings, charger: ChargerState | None) -> SpeechProfile:
    s = settings.stt
    return choose_profile(s.profile, s.gpu_model, s.cpu_model, charger)


async def load_stt(settings: Settings, profile: SpeechProfile, output: Output) -> WhisperSTT:
    """Load Whisper for the profile. If the GPU can't be used, fall back to the CPU model."""
    if profile.device == "cuda":
        await asyncio.to_thread(preload_cuda_libraries)
    stt = WhisperSTT(profile, settings.data_dir / "whisper", settings.stt.cpu_threads)
    output(f"Loading speech recognition ({profile.model} on {profile.device})...")
    try:
        await asyncio.to_thread(stt.load)
        return stt
    except Exception as e:
        if profile.device != "cuda":
            raise
        output(f"The GPU model didn't load ({e}); using the CPU model instead.")
        cpu = _profile_cpu(settings)
        return await load_stt(settings, cpu, output)


def _profile_cpu(settings: Settings) -> SpeechProfile:
    return choose_profile("cpu", settings.stt.gpu_model, settings.stt.cpu_model, None)


@dataclass
class _PowerSwitch:
    settings: Settings
    orchestrator: Orchestrator
    output: Output
    loading: asyncio.Task[None] | None = None

    def changed(self, charger: ChargerState | None) -> None:
        profile = _profile(self.settings, charger)
        current = getattr(self.orchestrator.stt, "profile", None)
        if profile == current or (self.loading and not self.loading.done()):
            return
        state = "on charger" if charger and charger.online else "on battery"
        self.output(f"Power: {state}, switching speech recognition to {profile.model}.")
        self.loading = asyncio.create_task(self._swap(profile))

    async def _swap(self, profile: SpeechProfile) -> None:
        try:
            # The old model keeps working until the new one is ready.
            self.orchestrator.stt = await load_stt(self.settings, profile, self.output)
        except Exception:
            log.exception("couldn't switch speech recognition model")


async def run_voice(settings: Settings, mode: str | None = None, output: Output = _print) -> None:
    mode = mode or settings.voice.mode
    output("Loading the voice...")
    speaker = await make_speaker(settings)
    stt = await load_stt(settings, _profile(settings, read_charger()), output)
    vad = SileroVAD()

    wake = None
    if mode == "wake":
        try:
            wake = WakeWord(settings.voice.wakeword_model, settings.voice.wakeword_threshold)
        except Exception as e:
            output(f"The wake word didn't load ({e}); use the hotkey instead.")

    mic = Microphone(settings.voice.mic_device)
    orchestrator = Orchestrator(
        settings.voice,
        mic,
        vad,
        stt,
        speaker,
        wake=wake,
        output=output,
        timing_path=settings.state_dir / "timing.log",
    )
    noah = await start(settings, confirm=orchestrator.confirm, say=orchestrator.announce)
    orchestrator.agent = noah.agent
    orchestrator.side_agent = noah.side_agent()
    orchestrator.keep_running = lambda: bool(noah.tasks.open_tasks())
    orchestrator.on_turn_end = noah.announcer.flush
    noah.controls.attach(orchestrator)

    async def stop_cancelled(event: TaskEvent) -> None:
        if event.task.finished and event.item and event.item.note == "cancelled":
            orchestrator.cancel_background()

    noah.tasks.subscribers.append(stop_cancelled)

    ptt = PushToTalk(settings.voice.ptt_key, orchestrator.ptt_down, orchestrator.ptt_up)
    try:
        ptt.start()
    except Exception as e:
        ptt = None
        output(f"The push-to-talk hotkey isn't available ({e}).")
        if wake is None:
            msg = "Neither the wake word nor the hotkey works, so Noah can't listen."
            raise SystemExit(msg) from None

    switch = _PowerSwitch(settings, orchestrator, output)
    watcher = asyncio.create_task(watch_charger(switch.changed))
    mic.start()

    hints = []
    if wake:
        hints.append(f'say "{settings.voice.wakeword_model.replace("_", " ")}"')
    if ptt:
        hints.append(f"hold {settings.voice.ptt_key}")
    output(f"Noah is listening: {' or '.join(hints)}. Ctrl-C quits.")
    if unfinished := noah.tasks.open_tasks():
        names = ", ".join(t.title for t in unfinished)
        output(f"Unfinished checklists from before: {names}.")

    try:
        await orchestrator.run()
    finally:
        watcher.cancel()
        with contextlib.suppress(BaseException):
            await watcher
        if ptt:
            ptt.stop()
        mic.close()
        await orchestrator.close()
        await noah.close()
        await speaker.close()
