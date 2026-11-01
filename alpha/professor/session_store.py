"""Keeps classes on disk so they can be resumed, and exports notes when asked."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .professor_mode import ROOT, Lesson, ProfessorSetupError

log = logging.getLogger(__name__)

SESSIONS = ROOT / "sessions"
LONGEST_NAME_PART = 80
_NOT_IN_A_FILE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class SavedClass:
    path: Path
    data: dict

    @property
    def label(self) -> str:
        return f"{self.data['lecture']} (slide {self.data['pointer']} of {self.data['total']})"


def _file_for(folder: Path, course_id: str, lecture: str) -> Path:
    return folder / f"{re.sub(r'[^\w-]+', '_', f'{course_id}-{lecture}')[:LONGEST_NAME_PART]}.json"


def save(lesson: Lesson, folder: Path | None = None) -> Path:
    """Written beside the real file first, so a crash cannot leave half a class."""
    folder = folder or SESSIONS
    folder.mkdir(parents=True, exist_ok=True)
    path = _file_for(folder, lesson.course.id, lesson.lecture)
    waiting = path.with_suffix(".tmp")
    waiting.write_text(json.dumps(lesson.to_saved(), ensure_ascii=False), encoding="utf-8")
    os.replace(waiting, path)
    return path


def saved_classes(folder: Path | None = None) -> list[SavedClass]:
    """The newest first. A damaged file is skipped, not fatal."""
    folder = folder or SESSIONS
    found = []
    for path in sorted(folder.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True) if folder.is_dir() else []:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if all(key in data for key in ("course_id", "lecture", "files", "pointer", "total")):
                found.append(SavedClass(path, data))
        except (OSError, ValueError, TypeError):
            log.warning("Skipped a saved class that could not be read: %s", path.name)
    return found


def _for_a_name(text: str) -> str:
    return " ".join(_NOT_IN_A_FILE_NAME.sub(" ", text).split()).strip(" .")[:LONGEST_NAME_PART]


def export_notes(lesson: Lesson, today: date | None = None) -> Path:
    """Only ever runs when asked. It never replaces an earlier export."""
    folder = Path(lesson.course.folder)
    if not folder.is_dir():
        raise ProfessorSetupError(f"The course folder was not found: {folder}")
    if not lesson.transcript:
        raise ProfessorSetupError("There is nothing to export yet.")
    stem = f"[ALPHA Notes] {_for_a_name(lesson.course.name)} - {_for_a_name(lesson.lecture)} - {today or date.today()}"
    path = folder / f"{stem}.md"
    number = 2
    while path.exists():
        path = folder / f"{stem} ({number}).md"
        number += 1
    heading = f"# {lesson.course.name}\n\n**Lecture:** {lesson.lecture}\n\n**Goal:** {lesson.goal}\n\n"
    path.write_text(heading + "\n\n---\n\n".join(lesson.transcript) + "\n", encoding="utf-8")
    return path
