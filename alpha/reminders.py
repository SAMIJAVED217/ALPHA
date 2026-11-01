"""Reminders kept on this computer. A reminder missed while APLHA was closed fires at the next start."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

from . import paths

log = logging.getLogger(__name__)

STAMP = "%Y-%m-%d %H:%M"


@dataclass
class Reminder:
    id: int
    text: str
    due: str

    @property
    def due_at(self) -> datetime:
        return datetime.strptime(self.due, STAMP)


class ReminderBook:
    """Used only on the tools thread, so it needs no lock."""

    def __init__(self):
        self._items: list[Reminder] | None = None

    @staticmethod
    def _file():
        return paths.data_dir() / "reminders.json"

    @property
    def items(self) -> list[Reminder]:
        if self._items is None:
            self._items = self._load()
        return self._items

    def _load(self) -> list[Reminder]:
        try:
            return [Reminder(**item) for item in json.loads(self._file().read_text(encoding="utf-8"))]
        except FileNotFoundError:
            return []
        except (ValueError, TypeError):
            log.warning("The reminders could not be read, so the list starts empty.")
            return []

    def _save(self) -> None:
        partial = self._file().with_suffix(".tmp")
        partial.write_text(json.dumps([asdict(item) for item in self.items]), encoding="utf-8")
        partial.replace(self._file())

    def add(self, text: str, due: datetime) -> Reminder:
        reminder = Reminder(max((item.id for item in self.items), default=0) + 1, text, due.strftime(STAMP))
        self.items.append(reminder)
        self._save()
        return reminder

    def cancel(self, reminder_id: int) -> bool:
        kept = [item for item in self.items if item.id != reminder_id]
        found = len(kept) != len(self.items)
        if found:
            self._items = kept
            self._save()
        return found

    def take_due(self, now: datetime) -> list[Reminder]:
        due = [item for item in self.items if item.due_at <= now]
        if due:
            self._items = [item for item in self.items if item.due_at > now]
            self._save()
        return due

    def upcoming(self) -> list[Reminder]:
        return sorted(self.items, key=lambda item: item.due_at)
