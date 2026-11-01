"""Hearing and speaking on this computer, using the two speech helper processes."""

from __future__ import annotations

import json
import logging
import queue
import re
import struct
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

_NO_WINDOW = 0x08000000
_PROJECT = str(Path(__file__).resolve().parent.parent.parent)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
_SHORTEST_PHRASE = 40


def take_sentences(text: str) -> tuple[list[str], str]:
    """Splits off finished sentences, and returns them with what is left over."""
    parts = _SENTENCE_END.split(text)
    done = [part.strip() for part in parts[:-1] if part.strip()]
    return done, parts[-1]


def _start(arguments: list[str]) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", *arguments],
        cwd=_PROJECT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW,
    )


class Transcriber:
    """Sound goes in during a turn, and one piece of text comes back when it ends."""

    def __init__(self, model_dir: Path, language: str, on_text: Callable[[str], None], on_exit: Callable[[], None]):
        self.language = language
        self._on_text = on_text
        self._on_exit = on_exit
        self._waiting: queue.Queue[bytes | None] = queue.Queue()
        self._stopping = False
        self._process = _start(["alpha.voice.stt_worker", str(model_dir), language])
        threading.Thread(target=self._write, name="alpha-stt-writer", daemon=True).start()
        threading.Thread(target=self._read, name="alpha-stt-reader", daemon=True).start()

    def feed(self, chunk: bytes) -> None:
        self._waiting.put(chunk)

    def finish(self) -> None:
        self._waiting.put(b"")

    def _write(self) -> None:
        while (chunk := self._waiting.get()) is not None:
            try:
                self._process.stdin.write(struct.pack("<I", len(chunk)) + chunk)
                self._process.stdin.flush()
            except (OSError, ValueError):
                return

    def _read(self) -> None:
        for line in self._process.stdout:
            event = json.loads(line)
            if event.get("event") == "text":
                self._on_text(event["text"])
        if not self._stopping:
            log.warning("The offline listener stopped unexpectedly.")
            self._on_exit()

    def stop(self) -> None:
        self._stopping = True
        self._waiting.put(None)
        self._process.terminate()


class Narrator:
    """Text goes in, and sound comes back in the same order for the speaker to play.

    Sound for anything said before `clear` is thrown away as it arrives.
    """

    def __init__(self, on_sound: Callable[[bytes], None], on_idle: Callable[[], None], on_exit: Callable[[], None]):
        self._on_sound = on_sound
        self._on_idle = on_idle
        self._on_exit = on_exit
        self._lock = threading.Lock()
        self._pending = 0
        self._discard = 0
        self._stopping = False
        self.voice = "David"
        self._process = _start(["alpha.voice.tts_worker"])
        threading.Thread(target=self._read, name="alpha-tts-reader", daemon=True).start()

    @property
    def busy(self) -> bool:
        with self._lock:
            return self._pending > 0

    def say(self, text: str) -> None:
        with self._lock:
            self._pending += 1
        try:
            self._process.stdin.write((json.dumps({"text": text, "voice": self.voice}) + "\n").encode("utf-8"))
            self._process.stdin.flush()
        except (OSError, ValueError):
            log.warning("The offline voice could not be reached.")

    def clear(self) -> None:
        with self._lock:
            self._discard = self._pending

    def _read(self) -> None:
        while True:
            head = self._process.stdout.read(4)
            if len(head) < 4:
                break
            sound = self._process.stdout.read(struct.unpack("<I", head)[0])
            with self._lock:
                self._pending -= 1
                dropped = self._discard > 0
                if dropped:
                    self._discard -= 1
                idle = self._pending == 0
            if not dropped:
                self._on_sound(sound)
            if idle:
                self._on_idle()
        if not self._stopping:
            log.warning("The offline voice stopped unexpectedly.")
            self._on_exit()

    def stop(self) -> None:
        self._stopping = True
        self._process.terminate()
