"""The parts of hands-free listening that are not the conversation itself."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
from collections import deque
from enum import Enum
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

CHUNK_SECONDS = 0.1
_NO_WINDOW = 0x08000000
_MOST_WAITING_CHUNKS = 20


class Mode(str, Enum):
    OFF = "off"
    WAITING = "waiting"
    TURN = "turn"
    REPLYING = "replying"
    FOLLOW_UP = "follow_up"

    @property
    def sends_to_gemini(self) -> bool:
        return self is Mode.TURN


class SpeechTracker:
    """Decides from loudness alone when someone has started and finished speaking.

    It follows the room's background noise, so a noisy fan is not mistaken for speech.
    """

    SILENCE_TO_END = 0.9
    WAIT_FOR_SPEECH = 5.0
    LONGEST_TURN = 25.0
    QUIETEST_SPEECH = 0.04

    def __init__(self):
        self.noise = 0.01
        self.reset()

    def reset(self) -> None:
        self.speech_seen = False
        self._quiet = 0.0
        self._elapsed = 0.0

    def is_speech(self, level: float) -> bool:
        return level > max(self.QUIETEST_SPEECH, self.noise * 3.0)

    def is_clear_speech(self, level: float) -> bool:
        return level > max(self.QUIETEST_SPEECH * 2.0, self.noise * 4.0)

    def hear_room(self, level: float) -> None:
        """Slowly learns a steady sound such as a fan, which brief speech barely moves."""
        self.noise += (level - self.noise) * 0.002

    def feed(self, level: float) -> bool:
        """Returns True when the turn should end."""
        self._elapsed += CHUNK_SECONDS
        if self.is_speech(level):
            self.speech_seen = True
            self._quiet = 0.0
        else:
            self._quiet += CHUNK_SECONDS
            self.noise += (level - self.noise) * 0.05
        if self._elapsed >= self.LONGEST_TURN:
            return True
        if self.speech_seen:
            return self._quiet >= self.SILENCE_TO_END
        return self._elapsed >= self.WAIT_FOR_SPEECH


class WakeProcess:
    """Runs `alpha.voice.wake_worker` and passes sound to it without ever blocking.

    If the listener falls behind, the oldest sound is dropped rather than held up.
    """

    def __init__(self, model_dir: Path, on_ready: Callable[[], None], on_wake: Callable[[], None],
                 on_exit: Callable[[], None], keyword: str = ""):
        self._on_ready = on_ready
        self._on_wake = on_wake
        self._on_exit = on_exit
        self.on_decision: Callable[[str], None] | None = None
        self._waiting: deque[bytes] = deque(maxlen=_MOST_WAITING_CHUNKS)
        self._wanted = threading.Event()
        self._stopping = False
        self._process = subprocess.Popen(
            [sys.executable, "-m", "alpha.voice.wake_worker", str(model_dir)],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
            env={**os.environ, "ALPHA_OWNER_KEYWORD": keyword},  # Not in the command line, where it would show.
        )
        threading.Thread(target=self._write, name="alpha-wake-writer", daemon=True).start()
        threading.Thread(target=self._read, name="alpha-wake-reader", daemon=True).start()

    def feed(self, chunk: bytes) -> None:
        self._waiting.append(chunk)
        self._wanted.set()

    def _write(self) -> None:
        while not self._stopping:
            self._wanted.wait(0.5)
            self._wanted.clear()
            while self._waiting:
                try:
                    self._process.stdin.write(self._waiting.popleft())
                    self._process.stdin.flush()
                except (OSError, ValueError):
                    return

    def _read(self) -> None:
        for line in self._process.stdout:
            try:
                event = json.loads(line).get("event")
            except ValueError:
                continue
            if event == "ready":
                self._on_ready()
            elif event == "wake":
                self._on_wake()
            elif event in ("confirm", "cancel", "keyword") and self.on_decision is not None:
                self.on_decision(event)
            elif event == "keyword_unknown" and self.on_decision is not None:
                self.on_decision("unknown")
        if not self._stopping:
            log.warning("The wake word listener stopped unexpectedly.")
            self._on_exit()

    def stop(self) -> None:
        self._stopping = True
        self._wanted.set()
        try:
            self._process.stdin.close()
        except OSError:
            pass
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
