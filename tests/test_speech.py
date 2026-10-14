import asyncio
import threading

import numpy as np
import pytest

from noah.audio.player import Player
from noah.speech import SentenceSplitter, Speaker, clean_for_speech


def test_splitter_yields_sentences_as_they_complete():
    s = SentenceSplitter()
    assert s.feed("Hello there") == []
    assert s.feed(". How are") == ["Hello there."]
    assert s.feed(" you? I'm") == ["How are you?"]
    assert s.feed(" fine, e.g. great.\nNew line") == ["I'm fine, e.g. great."]
    assert s.flush() == "New line"
    assert s.flush() is None


def test_splitter_keeps_decimals_and_paths_together():
    s = SentenceSplitter()
    assert s.feed("Python 3.13 is in ~/codes/noah. Done! ") == [
        "Python 3.13 is in ~/codes/noah.",
        "Done!",
    ]


def test_splitter_only_treats_a_period_as_an_abbreviation():
    s = SentenceSplitter()
    assert s.feed("Was that a yes or a no? Dr. Who is on. ") == [
        "Was that a yes or a no?",
        "Dr. Who is on.",
    ]


def test_clean_for_speech():
    assert clean_for_speech("**Done** — see `~/codes/x`") == "Done , see home/codes/x"
    assert clean_for_speech("- first item") == "first item"


class FakeTTS:
    sample_rate = 100

    def __init__(self):
        self.said: list[str] = []

    def synthesize(self, text: str):
        self.said.append(text)
        return np.ones(len(text), dtype=np.float32)


class FakeStream:
    """Stands in for a sounddevice stream; a thread drives the callback like PortAudio."""

    def __init__(self, callback, blocksize, **_):
        self.callback = callback
        self.blocksize = blocksize
        self.out: list[np.ndarray] = []
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        import time

        while self.running:
            block = np.zeros((self.blocksize, 1), dtype=np.float32)
            self.callback(block, self.blocksize, None, None)
            if block.any():
                self.out.append(block[:, 0].copy())
            time.sleep(0.001)

    def stop(self):
        self.running = False

    def close(self):
        pass


def _player(streams: list[FakeStream]) -> Player:
    def factory(**kw):
        stream = FakeStream(**kw)
        streams.append(stream)
        return stream

    return Player(100, stream_factory=factory, blocksize=8)


async def test_player_plays_queued_clips_in_order():
    streams: list[FakeStream] = []
    player = _player(streams)
    a = asyncio.create_task(player.play(np.full(10, 0.5, dtype=np.float32)))
    b = asyncio.create_task(player.play(np.full(5, 0.25, dtype=np.float32)))
    assert await asyncio.wait_for(a, 2) and await asyncio.wait_for(b, 2)
    played = np.concatenate(streams[0].out)
    assert list(played[played != 0]) == [0.5] * 10 + [0.25] * 5
    player.close()


async def test_player_stop_cuts_off_playback():
    streams: list[FakeStream] = []
    player = _player(streams)
    task = asyncio.create_task(player.play(np.ones(100_000, dtype=np.float32)))
    await asyncio.sleep(0.02)
    player.stop()
    assert await asyncio.wait_for(task, 1) is False
    assert not player.playing
    player.close()


async def test_speaker_speaks_streamed_sentences_and_announcements():
    tts = FakeTTS()
    streams: list[FakeStream] = []
    speaker = Speaker(tts, _player(streams))
    speaker.feed("First one. Sec")
    speaker.feed("ond one")
    speaker.say("Two of three done.")
    await asyncio.wait_for(speaker.finish(), 2)
    assert tts.said == ["First one.", "Two of three done.", "Second one"]
    assert speaker.timing.sentence_to_audio is not None
    await speaker.close()


async def test_speaker_stop_drops_everything():
    tts = FakeTTS()
    speaker = Speaker(tts, _player([]))
    speaker.feed("One. Two. Three. ")
    speaker.stop()
    await asyncio.wait_for(speaker.finish(), 1)
    assert not speaker.speaking
    await speaker.close()


@pytest.mark.parametrize("text", ["", "   ", "``"])
async def test_speaker_ignores_empty_text(text: str):
    speaker = Speaker(FakeTTS(), _player([]))
    speaker.say(text)
    assert not speaker.speaking
    await speaker.close()
