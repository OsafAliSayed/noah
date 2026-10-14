"""Audio playback through one output stream that can be stopped instantly (for barge-in).

The stream's callback runs on PortAudio's thread and pulls from a queue of sample buffers;
`play()` awaits the moment its buffer has been fully handed to the device.
"""

import asyncio
import collections
import threading
from collections.abc import Callable
from typing import Any

import numpy as np

from noah.tts.base import Samples

StreamFactory = Callable[..., Any]


def _sounddevice_stream(**kwargs: Any) -> Any:
    import sounddevice

    return sounddevice.OutputStream(**kwargs)


class _Clip:
    def __init__(self, samples: Samples, done: Callable[[bool], None]) -> None:
        self.samples = samples
        self.pos = 0
        self.done = done


class Player:
    def __init__(
        self,
        sample_rate: int,
        stream_factory: StreamFactory = _sounddevice_stream,
        blocksize: int = 1024,
    ) -> None:
        self.sample_rate = sample_rate
        self.stream_factory = stream_factory
        self.blocksize = blocksize
        self._clips: collections.deque[_Clip] = collections.deque()
        self._lock = threading.Lock()
        self._stream: Any = None

    @property
    def playing(self) -> bool:
        with self._lock:
            return bool(self._clips)

    def _ensure_stream(self) -> None:
        if self._stream is None:
            self._stream = self.stream_factory(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self.blocksize,
                callback=self._callback,
            )
            self._stream.start()

    def _callback(self, outdata: Any, frames: int, time: Any, status: Any) -> None:
        out = outdata[:, 0] if outdata.ndim == 2 else outdata
        filled = 0
        finished: list[_Clip] = []
        with self._lock:
            while filled < frames and self._clips:
                clip = self._clips[0]
                take = min(frames - filled, len(clip.samples) - clip.pos)
                out[filled : filled + take] = clip.samples[clip.pos : clip.pos + take]
                clip.pos += take
                filled += take
                if clip.pos >= len(clip.samples):
                    finished.append(self._clips.popleft())
        out[filled:] = 0
        for clip in finished:
            clip.done(True)

    async def play(self, samples: Samples) -> bool:
        """Play samples after anything already queued. Returns False if stopped first."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[bool] = loop.create_future()

        def done(completed: bool) -> None:
            loop.call_soon_threadsafe(_resolve, future, completed)

        clip = _Clip(np.asarray(samples, dtype=np.float32), done)
        if len(clip.samples) == 0:
            return True
        with self._lock:
            self._clips.append(clip)
        self._ensure_stream()
        return await future

    def stop(self) -> None:
        """Drop everything queued or playing, at once."""
        with self._lock:
            clips = list(self._clips)
            self._clips.clear()
        for clip in clips:
            clip.done(False)

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None


def _resolve(future: asyncio.Future[bool], value: bool) -> None:
    if not future.done():
        future.set_result(value)
