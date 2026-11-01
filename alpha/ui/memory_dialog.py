"""See, search, edit, export and delete everything ALPHA remembers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from ..memory import MemoryStore
from .style import dark_title_bar


class MemoryDialog(QDialog):
    def __init__(self, store: MemoryStore, parent=None):
        super().__init__(parent)
        self.setWindowTitle("APLHA memory")
        self.setMinimumSize(560, 460)
        self._store = store

        heading = QLabel("What APLHA remembers")
        heading.setObjectName("heading")
        note = QLabel("Kept only on this computer. A memory reaches Gemini only when it answers a question.")
        note.setObjectName("detail")
        note.setWordWrap(True)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search memories")
        self.search.textChanged.connect(self.refresh)
        self.rows = QListWidget()
        self.rows.setWordWrap(True)
        self.count = QLabel()
        self.count.setObjectName("detail")

        edit = QPushButton("Edit")
        edit.clicked.connect(self._edit)
        delete = QPushButton("Delete")
        delete.clicked.connect(self._delete)
        export = QPushButton("Export")
        export.clicked.connect(self._export)
        clear = QPushButton("Delete all")
        clear.clicked.connect(self._delete_all)
        done = QPushButton("Close")
        done.clicked.connect(self.accept)
        buttons = QHBoxLayout()
        for button in (edit, delete, export, clear):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(done)

        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(note)
        layout.addWidget(self.search)
        layout.addWidget(self.rows, 1)
        layout.addWidget(self.count)
        layout.addLayout(buttons)
        self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    def refresh(self) -> None:
        query = self.search.text().strip()
        memories = self._store.search(query, limit=500) if query else self._store.all()
        self.rows.clear()
        for memory in memories:
            when = datetime.fromtimestamp(memory.created).strftime("%d %b %Y")
            row = QListWidgetItem(f"{memory.text}\n{when}")
            row.setData(Qt.ItemDataRole.UserRole, (memory.id, memory.text))
            self.rows.addItem(row)
        self.count.setText(f"{len(memories)} shown")

    def _chosen(self):
        row = self.rows.currentItem()
        return row.data(Qt.ItemDataRole.UserRole) if row else None

    def _edit(self) -> None:
        chosen = self._chosen()
        if chosen:
            changed, ok = QInputDialog.getText(self, "Edit memory", "Memory", text=chosen[1])
            if ok and changed.strip():
                self._store.update(chosen[0], changed)
                self.refresh()

    def _delete(self) -> None:
        chosen = self._chosen()
        if chosen:
            self._store.delete(chosen[0])
            self.refresh()

    def _delete_all(self) -> None:
        answer = QMessageBox.question(self, "APLHA", "Delete every memory? This cannot be undone.")
        if answer == QMessageBox.StandardButton.Yes:
            self._store.delete_all()
            self.refresh()

    def _export(self) -> None:
        chosen, _ = QFileDialog.getSaveFileName(self, "Export memories", "alpha-memories.json", "JSON (*.json)")
        if chosen:
            self.count.setText(f"Exported {self._store.export(Path(chosen))} memories.")
