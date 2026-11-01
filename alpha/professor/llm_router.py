"""Professor Mode's calls to Gemini. Slide teaching never moves to another model.

These are ordinary text calls, separate from the live voice session, so nothing here
can change APLHA's normal persona or conversation.
"""

from __future__ import annotations

import logging
import time
from typing import Callable

from .. import keystore

log = logging.getLogger(__name__)

MOST_TRIES = 3
FIRST_WAIT_SECONDS = 4.0
TIMEOUT_MS = 400_000
_TRY_AGAIN_CODES = (429, 500, 502, 503, 504)


class TeachingError(Exception):
    """A call failed. The message is shown to the owner and never contains the key."""


class SlidesRefused(TeachingError):
    """Gemini would not take the slides in this form, so they can be sent another way."""


def _real_client(key: str):
    from google import genai

    return genai.Client(api_key=key, http_options={"timeout": TIMEOUT_MS})


class ProfessorModel:
    def __init__(self, model: str, load_key=keystore.load_key, make_client=_real_client,
                 on_wait: Callable[[str], None] = lambda _message: None, sleep=time.sleep):
        self._model = model
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
        for attempt in range(1, MOST_TRIES + 1):
            try:
                response = self._client.models.generate_content(
                    model=self._model, contents=contents, config={"system_instruction": system}
                )
            except Exception as error:
                code = getattr(error, "code", None)
                log.warning("Gemini call failed: %s %s", type(error).__name__, code)
                if code in _TRY_AGAIN_CODES and attempt < MOST_TRIES:
                    wait = FIRST_WAIT_SECONDS * 2 ** (attempt - 1)
                    self._on_wait(f"Gemini is busy. Trying again in {wait:.0f} seconds ({attempt} of {MOST_TRIES - 1}).")
                    self._sleep(wait)
                    continue
                if code in _TRY_AGAIN_CODES:
                    raise TeachingError("Gemini is still busy or the free limit is reached. Try next again later.") from error
                if code == 400 and files:
                    raise SlidesRefused("Gemini would not read the slides in that form.") from error
                raise TeachingError(f"Gemini could not answer ({type(error).__name__}).") from error
            if response.text:
                return response.text
            raise TeachingError("Gemini sent back an empty answer. Try again.")
        raise TeachingError("Gemini could not answer.")
