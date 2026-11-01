"""Translates a whole page, as closely as the language allows, and turns it into speech.

This is separate from Professor Mode, which teaches, and from live translation, which
listens. Gemini reads the page itself, and its speech model covers languages Windows has
no voice for, such as Urdu.
"""

from __future__ import annotations

import io
import re
import wave

import numpy as np

from .professor.llm_router import ProfessorModel, TeachingError

READER_MODEL = "gemini-3.8-flash"
VOICE_MODEL = "gemini-3.8-flash-tts"
VOICE_NAME = "Kore"
NO_TEXT = "NO TEXT"
LONGEST_PIECE = 900
OUTPUT_RATE = 24_000
_SENTENCE_END = re.compile(r"(?<=[.!?。۔؟])\s+|\n+")


def _rule(language: str) -> str:
    return (
        f"Translate all the text on this page into {language}, in reading order, as close to word for word as "
        f"correct {language} allows. Do not summarise, explain, skip or add anything. Keep numbers, names, units and "
        "abbreviations exact. If part of the page cannot be read, write [unreadable] there instead of guessing. "
        f"Output only the translation as plain text, with no Markdown. If the page has no text, write only {NO_TEXT}."
    )


def pieces(text: str) -> list[str]:
    """Splits at sentence ends, because a speech request can only hold so much."""
    found: list[str] = []
    current = ""
    for sentence in filter(None, (part.strip() for part in _SENTENCE_END.split(text))):
        while len(sentence) > LONGEST_PIECE:
            found.append(sentence[:LONGEST_PIECE])
            sentence = sentence[LONGEST_PIECE:]
        if current and len(current) + len(sentence) + 1 > LONGEST_PIECE:
            found.append(current)
            current = ""
        current = f"{current} {sentence}".strip()
    return [*found, current] if current else found


def as_speaker_sound(data: bytes, kind: str) -> bytes:
    """Sound for the speakers, whichever form the speech model sends."""
    rate = OUTPUT_RATE
    if "wav" in kind:
        with wave.open(io.BytesIO(data)) as file:
            rate = file.getframerate()
            data = file.readframes(file.getnframes())
    else:
        stated = re.search(r"rate=(\d+)", kind)
        rate = int(stated[1]) if stated else rate
    if rate == OUTPUT_RATE:
        return data
    sound = np.frombuffer(data, np.int16)
    places = np.arange(0, len(sound), rate / OUTPUT_RATE)
    return np.interp(places, np.arange(len(sound)), sound).astype(np.int16).tobytes()


class PageReader:
    def __init__(self, model: ProfessorModel | None = None, voice: ProfessorModel | None = None, on_wait=lambda _message: None):
        self._model = model or ProfessorModel(READER_MODEL, on_wait=on_wait)
        self._voice = voice or ProfessorModel(VOICE_MODEL, on_wait=on_wait)

    def translate(self, files: list[tuple[bytes, str]], language: str) -> str:
        """Empty when the page has no text."""
        text = self._model.ask(_rule(language), [], "Translate this page.", files).strip()
        return "" if text == NO_TEXT else text

    def speak(self, text: str) -> bytes:
        data, kind = self._voice.speech(text, VOICE_NAME)
        return as_speaker_sound(data, kind)


__all__ = ["PageReader", "TeachingError", "as_speaker_sound", "pieces"]
