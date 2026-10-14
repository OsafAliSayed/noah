import numpy as np

from noah.audio.wakeword import SilenceGate, chunk_dbfs


def _chunk(amplitude: float) -> np.ndarray:
    return np.full(1280, amplitude, dtype=np.int16)


QUIET, LOUD = _chunk(10), _chunk(3000)


def test_levels():
    assert chunk_dbfs(QUIET) < -50 < chunk_dbfs(LOUD)


def test_gate_passes_audio_until_the_room_has_been_quiet():
    gate = SilenceGate(-50, quiet_chunks=3, preroll_chunks=2)
    assert [len(gate(QUIET)) for _ in range(3)] == [1, 1, 1]
    assert gate.closed
    assert gate(QUIET) == []


def test_gate_replays_the_preroll_when_sound_returns():
    gate = SilenceGate(-50, quiet_chunks=2, preroll_chunks=3)
    for _ in range(2):
        gate(QUIET)
    assert gate.closed
    for _ in range(5):
        assert gate(QUIET) == []
    replay = gate(LOUD)
    assert len(replay) == 3
    assert replay[-1] is LOUD
    assert not gate.closed
    assert gate(QUIET) == [QUIET]


def test_loud_audio_keeps_the_gate_open():
    gate = SilenceGate(-50, quiet_chunks=2)
    for _ in range(10):
        assert gate(QUIET if _ % 2 else LOUD)
    assert not gate.closed
