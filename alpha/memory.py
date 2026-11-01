"""Long-term memory, kept only on this computer in a small database.

Nothing here is ever sent anywhere by itself. A memory only reaches the model
when it is the answer to a `recall` search, so only relevant ones leave the machine.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import paths

MAX_TEXT_CHARS = 1_000
MOST_RECALLED = 5

_SENSITIVE = re.compile(
    r"password|passcode|passphrase|\bpin\b|\botp\b|secret|api[ _-]?key|token|"
    r"credit card|debit card|\bcvv\b|bank account|iban|passport|\bssn\b|social security|"
    r"medical|diagnos|\b(?:\d[ -]?){13,19}\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[^\W_]+", re.UNICODE)
_IGNORED_WORDS = {"the", "a", "an", "my", "is", "of", "to", "what", "do", "i", "you", "me", "and", "in"}


def is_sensitive(text: str) -> bool:
    return bool(_SENSITIVE.search(text))


@dataclass(frozen=True)
class Memory:
    id: int
    text: str
    created: float


class MemoryStore:
    """Safe to use from any thread."""

    def __init__(self, path: Path | None = None):
        self._path = path or (paths.data_dir() / "memory.sqlite")
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self._path, check_same_thread=False)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL)"
        )
        self._db.commit()

    def add(self, text: str) -> Memory:
        text = text.strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"A memory must be 1 to {MAX_TEXT_CHARS} characters.")
        now = time.time()
        with self._lock:
            cursor = self._db.execute("INSERT INTO memories (text, created) VALUES (?, ?)", (text, now))
            self._db.commit()
        return Memory(cursor.lastrowid, text, now)

    def all(self) -> list[Memory]:
        with self._lock:
            rows = self._db.execute("SELECT id, text, created FROM memories ORDER BY created DESC").fetchall()
        return [Memory(*row) for row in rows]

    def count(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

    def search(self, query: str, limit: int = MOST_RECALLED) -> list[Memory]:
        wanted = {word.lower() for word in _WORD.findall(query)} - _IGNORED_WORDS
        scored = []
        for memory in self.all():
            words = {word.lower() for word in _WORD.findall(memory.text)}
            score = sum(1 for word in wanted if word in words or any(word in other for other in words))
            if score:
                scored.append((score, memory.created, memory))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [memory for _, _, memory in scored[:limit]]

    def update(self, memory_id: int, text: str) -> None:
        text = text.strip()
        if not text or len(text) > MAX_TEXT_CHARS:
            raise ValueError(f"A memory must be 1 to {MAX_TEXT_CHARS} characters.")
        with self._lock:
            self._db.execute("UPDATE memories SET text = ? WHERE id = ?", (text, memory_id))
            self._db.commit()

    def delete(self, memory_id: int) -> bool:
        with self._lock:
            cursor = self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            self._db.commit()
        return cursor.rowcount > 0

    def delete_all(self) -> None:
        with self._lock:
            self._db.execute("DELETE FROM memories")
            self._db.commit()

    def export(self, target: Path) -> int:
        memories = self.all()
        rows = [{"id": item.id, "text": item.text, "created": item.created} for item in memories]
        target.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
        return len(rows)

    def close(self) -> None:
        with self._lock:
            self._db.close()
