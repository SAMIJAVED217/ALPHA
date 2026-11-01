"""Splits a reply into the one line that is spoken and the rest, which is only shown."""

from __future__ import annotations

import re
from dataclasses import dataclass

LONGEST_SPOKEN = 220
FALLBACK_SPOKEN = "The notes are on your screen."
_NOT_SPEECH = re.compile(r"[|=\\^$`]|^\s*(#|[-*>]|\d+[.)]\s)")


@dataclass(frozen=True)
class Reply:
    spoken: str
    screen: str


def split(text: str) -> Reply:
    """The prompt asks for a plain first line. A reply that ignores that is shown, not read aloud."""
    lines = text.strip().splitlines()
    first = lines[0].strip() if lines else ""
    if not first or len(first) > LONGEST_SPOKEN or _NOT_SPEECH.search(first):
        return Reply(FALLBACK_SPOKEN, text.strip())
    spoken = first.replace("*", "").replace("_", " ").strip()
    return Reply(spoken, "\n".join(lines[1:]).strip() or first)
