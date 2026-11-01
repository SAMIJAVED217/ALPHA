"""The languages APLHA can use, and what each speech engine can really do with them."""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

AUTO = "auto"
_MAIN_NAMES = {"en": "English", "ko": "Korean", "ur": "Urdu", "hi": "Hindi"}
# Every language the offline speech model can hear.
_OTHER_NAMES = {
    "af": "Afrikaans", "am": "Amharic", "ar": "Arabic", "as": "Assamese", "az": "Azerbaijani", "ba": "Bashkir",
    "be": "Belarusian", "bg": "Bulgarian", "bn": "Bengali", "bo": "Tibetan", "br": "Breton", "bs": "Bosnian",
    "ca": "Catalan", "cs": "Czech", "cy": "Welsh", "da": "Danish", "de": "German", "el": "Greek", "es": "Spanish",
    "et": "Estonian", "eu": "Basque", "fa": "Persian", "fi": "Finnish", "fo": "Faroese", "fr": "French",
    "gl": "Galician", "gu": "Gujarati", "ha": "Hausa",
    "haw": "Hawaiian", "he": "Hebrew", "hr": "Croatian", "ht": "Haitian Creole", "hu": "Hungarian", "hy": "Armenian",
    "id": "Indonesian", "is": "Icelandic", "it": "Italian", "ja": "Japanese", "jw": "Javanese", "ka": "Georgian",
    "kk": "Kazakh", "km": "Khmer", "kn": "Kannada", "la": "Latin", "lb": "Luxembourgish", "ln": "Lingala", "lo": "Lao",
    "lt": "Lithuanian", "lv": "Latvian", "mg": "Malagasy", "mi": "Maori", "mk": "Macedonian", "ml": "Malayalam",
    "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "mt": "Maltese", "my": "Burmese", "ne": "Nepali", "nl": "Dutch",
    "nn": "Norwegian Nynorsk", "no": "Norwegian", "oc": "Occitan", "pa": "Punjabi", "pl": "Polish", "ps": "Pashto",
    "pt": "Portuguese", "ro": "Romanian", "ru": "Russian", "sa": "Sanskrit", "sd": "Sindhi", "si": "Sinhala",
    "sk": "Slovak", "sl": "Slovenian", "sn": "Shona", "so": "Somali", "sq": "Albanian", "sr": "Serbian",
    "su": "Sundanese", "sv": "Swedish", "sw": "Swahili", "ta": "Tamil", "te": "Telugu", "tg": "Tajik", "th": "Thai",
    "tk": "Turkmen", "tl": "Tagalog", "tr": "Turkish", "tt": "Tatar", "uk": "Ukrainian", "uz": "Uzbek",
    "vi": "Vietnamese", "yi": "Yiddish", "yo": "Yoruba", "yue": "Cantonese", "zh": "Chinese",
}
NAMES = {**_MAIN_NAMES, **dict(sorted(_OTHER_NAMES.items(), key=lambda item: item[1]))}
MAIN_CHOICES = (*_MAIN_NAMES, AUTO)
MENU_NAMES = {
    "en": "English",
    "ko": "Korean (\ud55c\uad6d\uc5b4)",
    "ur": "Urdu (\u0627\u0631\u062f\u0648)",
    "hi": "Hindi (\u0939\u093f\u0928\u094d\u0926\u0940)",
    AUTO: "Match how I speak",
    **{code: name for code, name in NAMES.items() if code not in _MAIN_NAMES},
}
# Windows identifies a voice's language by a number, and its low ten bits name the language.
_WINDOWS_LANGUAGES = {
    0x01: "ar", 0x02: "bg", 0x03: "ca", 0x04: "zh", 0x05: "cs", 0x06: "da", 0x07: "de", 0x08: "el", 0x09: "en",
    0x0A: "es", 0x0B: "fi", 0x0C: "fr", 0x0D: "he", 0x0E: "hu", 0x0F: "is", 0x10: "it", 0x11: "ja", 0x12: "ko",
    0x13: "nl", 0x14: "no", 0x15: "pl", 0x16: "pt", 0x18: "ro", 0x19: "ru", 0x1A: "hr", 0x1B: "sk", 0x1C: "sq",
    0x1D: "sv", 0x1E: "th", 0x1F: "tr", 0x20: "ur", 0x21: "id", 0x22: "uk", 0x23: "be", 0x24: "sl", 0x25: "et",
    0x26: "lv", 0x27: "lt", 0x29: "fa", 0x2A: "vi", 0x2B: "hy", 0x2C: "az", 0x2D: "eu", 0x2F: "mk", 0x36: "af",
    0x37: "ka", 0x38: "fo", 0x39: "hi", 0x3E: "ms", 0x3F: "kk", 0x41: "sw", 0x43: "uz", 0x45: "bn", 0x46: "pa",
    0x47: "gu", 0x49: "ta", 0x4A: "te", 0x4B: "kn", 0x4C: "ml", 0x4E: "mr", 0x50: "mn", 0x56: "gl", 0x5B: "si",
    0x61: "ne", 0x63: "ps",
}
_VOICES_KEY = r"SOFTWARE\Microsoft\Speech\Voices\Tokens"
_SCRIPTS = (
    ("ko", 0xAC00, 0xD7A3), ("ko", 0x1100, 0x11FF), ("ja", 0x3040, 0x30FF), ("zh", 0x4E00, 0x9FFF),
    ("hi", 0x0900, 0x097F), ("bn", 0x0980, 0x09FF), ("pa", 0x0A00, 0x0A7F), ("gu", 0x0A80, 0x0AFF),
    ("ta", 0x0B80, 0x0BFF), ("te", 0x0C00, 0x0C7F), ("kn", 0x0C80, 0x0CFF), ("ml", 0x0D00, 0x0D7F),
    ("si", 0x0D80, 0x0DFF), ("th", 0x0E00, 0x0E7F), ("lo", 0x0E80, 0x0EFF), ("my", 0x1000, 0x109F),
    ("ka", 0x10A0, 0x10FF), ("am", 0x1200, 0x137F), ("km", 0x1780, 0x17FF), ("hy", 0x0530, 0x058F),
    ("he", 0x0590, 0x05FF), ("ru", 0x0400, 0x04FF), ("el", 0x0370, 0x03FF), ("ar", 0x0600, 0x06FF),
)
# Letters Urdu uses that Arabic does not, which tell the two apart.
_URDU_LETTERS = set("\u0679\u0688\u0691\u06BA\u06BE\u06C1\u06CC\u06D2")


def instruction(code: str) -> str:
    """Tells the model which language to reply in, for the session it starts."""
    if code == AUTO:
        return "Reply in whichever language your owner speaks to you."
    name = NAMES[code]
    script = " Write Urdu in Urdu script." if code == "ur" else " Write Hindi in Devanagari." if code == "hi" else ""
    if code == "en":
        return "Reply in English unless your owner asks for another language."
    return (
        f"Reply in {name}. Always speak and write in {name}, even when your owner writes "
        f"in another language, unless they ask you to change it.{script}"
    )


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
                    language = _WINDOWS_LANGUAGES.get(int(part.strip() or "0", 16) & 0x3FF)
                    if language:
                        found.setdefault(language, name)
    except (OSError, ValueError):
        log.warning("The Windows voices could not be listed.")
    return found


def language_of(text: str) -> str:
    """The language a reply is written in, judged by its script. Languages in Latin letters read as English."""
    if any("\u3040" <= letter <= "\u30ff" for letter in text):
        return "ja"
    for letter in text:
        point = ord(letter)
        for code, low, high in _SCRIPTS:
            if low <= point <= high:
                return "ur" if code == "ar" and _URDU_LETTERS & set(text) else code
    return "en"


def availability(code: str) -> str:
    """What each engine can do, shown beside the choice, so nothing is promised falsely."""
    if code == AUTO:
        return "Gemini follows your language. The offline voice uses whichever voices Windows has."
    voice = installed_voices().get(code)
    speaking = voice or f"Windows has no {NAMES[code]} voice, so replies show as text"
    return f"Gemini voice: yes. Offline listening: yes. Offline voice: {speaking}."
