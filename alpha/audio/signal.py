"""Small sound calculations. No hardware here."""

from __future__ import annotations

import numpy as np


def level_of(pcm16: bytes) -> float:
    """Loudness from 0 to 1, scaled so that ordinary speech fills most of the range."""
    if len(pcm16) < 2:
        return 0.0
    samples = np.frombuffer(pcm16, dtype="<i2", count=len(pcm16) // 2).astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(samples * samples)))
    return min(1.0, rms * 4.0)


class Resampler:
    """Changes the sample rate of a running stream of 16-bit mono sound.

    Going down it averages groups of samples first, so that high pitches do
    not fold back as noise. It keeps its place between chunks, so the joins
    are not audible.
    """

    def __init__(self, from_rate: int, to_rate: int):
        self.from_rate = from_rate
        self.to_rate = to_rate
        self._step = from_rate / to_rate
        self._position = 0.0
        self._last = 0.0
        self._smooth = max(1, int(round(self._step)))
        self._tail = np.zeros(self._smooth - 1, dtype=np.float32)

    def process(self, pcm16: bytes) -> bytes:
        if self.from_rate == self.to_rate:
            return pcm16
        fresh = np.frombuffer(pcm16, dtype="<i2", count=len(pcm16) // 2).astype(np.float32)
        if fresh.size == 0:
            return b""
        if self._smooth > 1:
            joined = np.concatenate((self._tail, fresh))
            window = np.ones(self._smooth, dtype=np.float32) / self._smooth
            smoothed = np.convolve(joined, window, mode="valid")
            self._tail = joined[-(self._smooth - 1) :]
        else:
            smoothed = fresh
        samples = np.concatenate(([self._last], smoothed))
        count = int(np.ceil((samples.size - 1 - self._position) / self._step))
        if count <= 0:
            self._position -= samples.size - 1
            self._last = float(samples[-1])
            return b""
        points = self._position + np.arange(count) * self._step
        result = np.interp(points, np.arange(samples.size), samples)
        self._position = float(points[-1] + self._step - (samples.size - 1))
        self._last = float(samples[-1])
        return np.clip(np.round(result), -32768, 32767).astype("<i2").tobytes()
