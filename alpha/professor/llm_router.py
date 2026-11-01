"""Professor Mode's calls to Gemini. Slide teaching never moves to another model.

These are ordinary text calls, separate from the live voice session, so nothing here
can change APLHA's normal persona or conversation.
"""

from __future__ import annotations

import logging
import math
import re
import time
from typing import Callable

from .. import keystore

log = logging.getLogger(__name__)

MOST_TRIES = 3
FIRST_WAIT_SECONDS = 4.0
TIMEOUT_MS = 400_000
LONGEST_WAIT_SECONDS = 60.0
DAILY_LIMIT_SECONDS = 600.0
_TRY_AGAIN_CODES = (429, 500, 502, 503, 504)
_WAIT_ASKED_FOR = re.compile(r"retry(?:delay)?\D{0,12}(\d+(?:\.\d+)?)\s*s", re.IGNORECASE)


class TeachingError(Exception):
    """A call failed. The message is shown to the owner and never contains the key."""


class SlidesRefused(TeachingError):
    """Gemini would not take the slides in this form, so they can be sent another way."""


def _real_client(key: str):
    from google import genai

    return genai.Client(api_key=key, http_options={"timeout": TIMEOUT_MS})


class ProfessorModel:
    def __init__(self, model: str, load_key=keystore.load_key, make_client=_real_client,
                 on_wait: Callable[[str], None] = lambda _message: None, sleep=time.sleep, spare: tuple = ()):
        self.model = model
        self._spare = list(spare)
        self._load_key = load_key
        self._make_client = make_client
        self._on_wait = on_wait
        self._sleep = sleep
        self._client = None

    def ask(self, system: str, history: list[tuple[str, str]], text: str,
            files: list[tuple[bytes, str]] | tuple = ()) -> str:
        key = self._load_key()
        if not key:
            raise TeachingError("Add your Gemini key in Settings first.")
        self._client = self._client or self._make_client(key)
        parts = [{"inline_data": {"data": data, "mime_type": kind}} for data, kind in files]
        contents = [{"role": role, "parts": [{"text": said}]} for role, said in history]
        contents.append({"role": "user", "parts": [*parts, {"text": text}]})
        response = self._call(contents, {"system_instruction": system}, bool(files))
        if response.text:
            return response.text
        raise TeachingError("Gemini sent back an empty answer. Try again.")

    def speech(self, text: str, voice: str) -> tuple[bytes, str]:
        """The sound for some text, with its kind, from a speech model."""
        key = self._load_key()
        if not key:
            raise TeachingError("Add your Gemini key in Settings first.")
        self._client = self._client or self._make_client(key)
        config = {
            "response_modalities": ["AUDIO"],
            "speech_config": {"voice_config": {"prebuilt_voice_config": {"voice_name": voice}}},
        }
        response = self._call(text, config, False)
        try:
            sound = response.candidates[0].content.parts[0].inline_data
            return sound.data, sound.mime_type or ""
        except (AttributeError, IndexError, TypeError) as error:
            raise TeachingError("Gemini sent back no sound.") from error

    def _call(self, contents, config: dict, has_files: bool):
        attempt = 0
        while attempt < MOST_TRIES:
            attempt += 1
            try:
                return self._client.models.generate_content(model=self.model, contents=contents, config=config)
            except Exception as error:
                code = getattr(error, "code", None)
                log.warning("Gemini call failed: %s %s: %s", type(error).__name__, code, str(error)[:300])
                wait = self._wait_asked_for(error)
                if code == 429 and wait >= DAILY_LIMIT_SECONDS:
                    # Only a day-long wait proves it. The word "day" also appears on short limits.
                    self._next_model(wait, error)
                    attempt = 0
                    continue
                if code in _TRY_AGAIN_CODES and attempt < MOST_TRIES:
                    self._count_down(self._wait_for(error, attempt), attempt)
                    continue
                if code in _TRY_AGAIN_CODES and self._spare:
                    self._on_wait(f"{self.model} is too busy, so switching to {self._spare[0]}.")
                    self.model = self._spare.pop(0)
                    attempt = 0
                    continue
                if code in _TRY_AGAIN_CODES:
                    raise TeachingError("Gemini is still busy. Your place is kept, so try again in a minute.") from error
                if code == 400 and has_files:
                    raise SlidesRefused("Gemini would not read the slides in that form.") from error
                raise TeachingError(f"Gemini could not answer ({type(error).__name__}).") from error
        raise TeachingError("Gemini could not answer.")

    @staticmethod
    def _wait_asked_for(error: Exception) -> float:
        asked = _WAIT_ASKED_FOR.search(str(error))
        return float(asked[1]) if asked else 0.0

    def _wait_for(self, error: Exception, attempt: int) -> float:
        """Doubles each time, or waits as long as Gemini asks, whichever is longer."""
        return min(max(FIRST_WAIT_SECONDS * 2 ** (attempt - 1), self._wait_asked_for(error)), LONGEST_WAIT_SECONDS)

    def _next_model(self, wait: float, error: Exception) -> None:
        """Always shown, and always another Gemini model, which can read pages too."""
        if not self._spare:
            hours = max(1, round(wait / 3600))
            raise TeachingError(f"Today's free Gemini limit is used up, for about {hours} more hours. Your place is saved.") from error
        used_up, self.model = self.model, self._spare.pop(0)
        log.warning("%s reached its daily limit, so using %s", used_up, self.model)
        self._on_wait(f"Today's free limit for {used_up} is used up, so switching to {self.model}.")

    def _count_down(self, wait: float, attempt: int) -> None:
        for left in range(math.ceil(wait), 0, -1):
            unit = "second" if left == 1 else "seconds"
            self._on_wait(f"Gemini is busy. Trying again in {left} {unit} (try {attempt + 1} of {MOST_TRIES}).")
            self._sleep(1)
