"""User settings, saved as JSON in ALPHA's data folder. Never holds API keys."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import paths
from .characters import CHARACTERS, DEFAULT_CHARACTER
from .languages import MENU_NAMES
from .state import DEFAULT_COLOURS

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-3.8-live"
DEFAULT_VOICE_KEY = "F9"
TALK_KEYS = ("F2", "F3", "F4", "F6", "F7", "F8", "F9", "F10", "F11", "F12")

SPEECH_ENGINES = ("gemini", "offline")
SPEECH_LANGUAGES = MENU_NAMES

_HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Settings:
    setup_complete: bool = False
    model: str = DEFAULT_MODEL
    microphone: str = ""
    speakers: str = ""
    talk_key: str = DEFAULT_VOICE_KEY
    speech_engine: str = "gemini"
    speech_language: str = "en"
    character: str = DEFAULT_CHARACTER
    hands_free_at_start: bool = True
    morning_briefing: bool = True
    skip_ads: bool = True
    translate_to: str = "en"
    last_briefing: str = ""
    colours: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLOURS))

    def colour_for(self, state_name: str) -> str:
        return self.colours.get(state_name, DEFAULT_COLOURS[state_name])


def _clean(raw: dict) -> Settings:
    settings = Settings()
    if isinstance(raw.get("setup_complete"), bool):
        settings.setup_complete = raw["setup_complete"]
    for name in ("model", "microphone", "speakers"):
        if isinstance(raw.get(name), str):
            setattr(settings, name, raw[name].strip())
    if not settings.model:
        settings.model = DEFAULT_MODEL
    if raw.get("speech_engine") in SPEECH_ENGINES:
        settings.speech_engine = raw["speech_engine"]
    for name in ("hands_free_at_start", "morning_briefing", "skip_ads"):
        if isinstance(raw.get(name), bool):
            setattr(settings, name, raw[name])
    if isinstance(raw.get("translate_to"), str):
        settings.translate_to = raw["translate_to"]
    if isinstance(raw.get("last_briefing"), str):
        settings.last_briefing = raw["last_briefing"]
    if raw.get("character") in CHARACTERS:
        settings.character = raw["character"]
    if raw.get("speech_language") in SPEECH_LANGUAGES:
        settings.speech_language = raw["speech_language"]
    if raw.get("talk_key") in TALK_KEYS:
        settings.talk_key = raw["talk_key"]
    saved = raw.get("colours")
    if isinstance(saved, dict):
        for state_name in DEFAULT_COLOURS:
            value = saved.get(state_name)
            if isinstance(value, str) and _HEX_COLOUR.match(value):
                settings.colours[state_name] = value
    return settings


def load(path: Path | None = None) -> Settings:
    path = path or paths.settings_file()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as error:
        log.warning("Could not read settings, using defaults: %s", error)
        return Settings()
    if not isinstance(raw, dict):
        log.warning("Settings file has the wrong shape, using defaults.")
        return Settings()
    return _clean(raw)


def save(settings: Settings, path: Path | None = None) -> None:
    """Writes to a neighbouring file first, so a crash cannot leave half a file."""
    path = path or paths.settings_file()
    partial = path.with_name(path.name + ".partial")
    partial.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(partial, path)
