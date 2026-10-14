"""One 16 kHz mono microphone stream, shared by the wake word, VAD and recorder.

The PortAudio callback hands each block to the event loop; every subscriber gets its own
bounded queue, so a slow consumer drops old audio instead of stalling the others.
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

import numpy as np
import numpy.typing as npt

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
FRAME = 512  # 32 ms: what Silero VAD takes; the wake word model buffers to 80 ms itself
BLOCK = 3 * FRAME  # read 96 ms at a time: fewer wakeups while idle, then split into frames

Frame = npt.NDArray[np.float32]
StreamFactory = Callable[..., Any]


def _sounddevice_stream(**kwargs: Any) -> Any:
    import sounddevice

    return sounddevice.InputStream(**kwargs)


class Microphone:
    def __init__(
        self,
        device: str | int | None = None,
        stream_factory: StreamFactory = _sounddevice_stream,
        queue_frames: int = 400,  # about 13 s of audio per subscriber
    ) -> None:
        self.device = device
        self.stream_factory = stream_factory
        self.queue_frames = queue_frames
        self._subscribers: list[asyncio.Queue[Frame]] = []
        self._stream: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stream = self.stream_factory(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            blocksize=BLOCK,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

    def subscribe(self) -> asyncio.Queue[Frame]:
        queue: asyncio.Queue[Frame] = asyncio.Queue(maxsize=self.queue_frames)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Frame]) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(queue)

    def _callback(self, indata: Any, frames: int, time: Any, status: Any) -> None:
        if status:
            log.debug("mic status: %s", status)
        block = np.array(indata[:, 0] if indata.ndim == 2 else indata, dtype=np.float32)
        chunks = [block[i : i + FRAME] for i in range(0, len(block) - FRAME + 1, FRAME)]
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self.publish, chunks)

    def publish(self, frames: list[Frame]) -> None:
        for frame in frames:
            for queue in self._subscribers:
                if queue.full():
                    queue.get_nowait()
                queue.put_nowait(frame)
