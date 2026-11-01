"""What APLHA has learned from the owner's corrections and praise, kept on this computer.

Each lesson has a score. Being repeated or helping raises it, and being wrong lowers it
until it is dropped. The strongest lessons go to Gemini with each session. They only
shape replies: every safety rule is in code, so nothing here can weaken one.
"""

from __future__ import annotations

import json
import logging

from . import paths

log = logging.getLogger(__name__)

LONGEST_LESSON = 300
MOST_KEPT = 200
MOST_SENT = 25


def _file():
    return paths.data_dir() / "lessons.json"


def load() -> list[dict]:
    try:
        saved = json.loads(_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except ValueError:
        log.warning("The lessons file could not be read.")
        return []
    return [item for item in saved if isinstance(item, dict) and isinstance(item.get("text"), str)]


def _save(lessons: list[dict]) -> None:
    strongest = sorted(lessons, key=lambda item: item.get("score", 1), reverse=True)[:MOST_KEPT]
    partial = _file().with_suffix(".tmp")
    partial.write_text(json.dumps(strongest, indent=2, ensure_ascii=False), encoding="utf-8")
    partial.replace(_file())


def learn(text: str, helped: bool = True) -> str:
    """Learning the same thing again strengthens it. A lesson that did not help fades, then goes."""
    words = " ".join(text.split())[:LONGEST_LESSON]
    lessons = load()
    match = next((item for item in lessons if item["text"].lower() == words.lower()), None)
    if match is None and not helped:
        return "That lesson was not known."
    if match is None:
        lessons.append({"text": words, "score": 1})
        outcome = f"Learned: {words}"
    else:
        match["score"] = match.get("score", 1) + (1 if helped else -1)
        outcome = f"Strengthened: {words}" if helped else f"Weakened: {words}"
        if match["score"] <= 0:
            lessons.remove(match)
            outcome = f"Dropped: {words}"
    _save(lessons)
    return outcome


def for_the_model() -> str:
    strongest = sorted(load(), key=lambda item: item.get("score", 1), reverse=True)[:MOST_SENT]
    if not strongest:
        return ""
    lines = "\n".join(f"- {item['text']}" for item in strongest)
    return f"What you have learned from your owner about how they like things done. Follow it.\n{lines}"
