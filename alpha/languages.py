"""The languages APLHA can use, and what each speech engine can really do with them."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

AUTO = "auto"
NAMES = {"en": "English", "ko": "Korean", "ur": "Urdu", "hi": "Hindi"}
MENU_NAMES = {
    "en": "English",
    "ko": "Korean (한국어)",
    "ur": "Urdu (اردو)",
    "hi": "Hindi (हिन्दी)",
    AUTO: "Match how I speak",
}
_WINDOWS_CODES = {"409": "en", "809": "en", "412": "ko", "420": "ur", "439": "hi"}
_VOICES_KEY = r"SOFTWARE\Microsoft\Speech\Voices\Tokens"
_SCRIPTS = (("ko", 0xAC00, 0xD7A3), ("ko", 0x1100, 0x11FF), ("hi", 0x0900, 0x097F), ("ur", 0x0600, 0x06FF))


def instruction(code: str) -> str:
    """Tells the model which language to reply in, for the session it starts."""
    if code == AUTO:
        return "Reply in whichever language your owner speaks to you."
    name = NAMES[code]
    script = " Write Urdu in Urdu script." if code == "ur" else " Write Hindi in Devanagari." if code == "hi" else ""
    return f"Reply in {name} unless your owner asks for another language.{script}"


def installed_voices() -> dict[str, str]:
    """Each language's first Windows voice, read from the registry so nothing has to load."""
    if sys.platform != "win32":
        return {}
    import winreg

    found: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _VOICES_KEY) as tokens:
            for index in range(winreg.QueryInfoKey(tokens)[0]):
                with winreg.OpenKey(tokens, rf"{winreg.EnumKey(tokens, index)}\Attributes") as details:
                    name = winreg.QueryValueEx(details, "Name")[0]
                    codes = winreg.QueryValueEx(details, "Language")[0]
                for part in codes.split(";"):
                    language = _WINDOWS_CODES.get(part.strip().lower().lstrip("0"))
                    if language:
                        found.setdefault(language, name)
    except OSError:
        log.warning("The Windows voices could not be listed.")
    return found


def language_of(text: str) -> str:
    """The language a reply is written in, judged by its script."""
    for letter in text:
        point = ord(letter)
        for code, low, high in _SCRIPTS:
            if low <= point <= high:
                return code
    return "en"


def availability(code: str) -> str:
    """What each engine can do, shown beside the choice, so nothing is promised falsely."""
    if code == AUTO:
        return "Gemini follows your language. The offline voice uses whichever voices Windows has."
    voice = installed_voices().get(code)
    speaking = voice or f"Windows has no {NAMES[code]} voice, so replies show as text"
    return f"Gemini voice: yes. Offline listening: yes. Offline voice: {speaking}."
