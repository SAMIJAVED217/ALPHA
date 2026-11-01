"""What the owner has chosen to tell APLHA about themselves. It stays in the data folder on this computer."""

from __future__ import annotations

import json
import logging

from . import paths

log = logging.getLogger(__name__)

LONGEST_ANSWER = 2_000
FIELDS = {
    "name": "Name, and what to call you",
    "city": "Home city",
    "languages": "Languages you speak",
    "work": "Study or work",
    "projects": "What you are working on now",
    "schedule": "Your usual week and deadlines",
    "goals": "Your goals",
    "people": "People who matter to you",
    "preferences": "How you like things done",
}


def _file():
    return paths.data_dir() / "about_me.json"


def load() -> dict[str, str]:
    try:
        saved = json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError:
        log.warning("The About me file could not be read.")
        return {}
    return {key: value for key, value in saved.items() if key in FIELDS and isinstance(value, str)}


def save(answers: dict[str, str]) -> None:
    cleaned = {key: value.strip()[:LONGEST_ANSWER] for key, value in answers.items() if key in FIELDS and value.strip()}
    partial = _file().with_suffix(".tmp")
    partial.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False), encoding="utf-8")
    partial.replace(_file())


def update(field: str, answer: str) -> None:
    save({**load(), field: answer})


def for_the_model() -> str:
    """Sent to Gemini at the start of each conversation, because that is what makes replies personal."""
    answers = load()
    if not answers:
        return ""
    lines = "\n".join(f"- {FIELDS[key]}: {answers[key]}" for key in FIELDS if key in answers)
    return (
        "What your owner has told you about themselves. Use it naturally to personalise "
        f"your help, and do not recite it back unasked.\n{lines}"
    )
