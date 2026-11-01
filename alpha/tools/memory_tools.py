"""Memory tools. Saving is visible, and sensitive details never save without a press."""

from __future__ import annotations

from ..memory import MemoryStore, is_sensitive
from .registry import INTEGER, STRING, Plan, Tool, ToolRefused, UndoStack, text, whole_number


def memory_tools(store: MemoryStore, undo: UndoStack | None = None) -> list[Tool]:
    undo = undo or UndoStack()

    def remember(arguments):
        content = text(arguments, "text", 1_000)
        if is_sensitive(content):
            raise ToolRefused("That looks sensitive, so use propose_remember_sensitive and let the owner decide.")
        saved = store.add(content)
        undo.push("the new memory removed", lambda: store.delete(saved.id))
        return {"ok": True, "id": saved.id, "detail": f"Remembered: {saved.text}"}

    def recall(arguments):
        found = store.search(text(arguments, "query", 200))
        return {
            "ok": True,
            "memories": [{"id": item.id, "text": item.text} for item in found],
            "detail": f"Looked in memory and found {len(found)} relevant.",
        }

    def plan_sensitive(arguments):
        content = text(arguments, "text", 1_000)

        def perform():
            return f"Remembered: {store.add(content).text}"

        return Plan("remember something sensitive", "It would stay on this computer, in ALPHA's memory.", content, perform)

    def plan_forget(arguments):
        memory_id = whole_number(arguments, "id", 1, 2**40)
        match = next((item for item in store.all() if item.id == memory_id), None)
        if match is None:
            raise ToolRefused("There is no memory with that id.")

        def perform():
            if not store.delete(memory_id):
                raise ToolRefused("That memory was already gone.")
            return f"Forgot: {match.text}"

        return Plan("delete a memory", "This cannot be undone.", match.text, perform)

    saying = {"text": (STRING, "One short fact, in plain words.")}
    return [
        Tool(
            "remember",
            "Save a lasting fact or preference the owner would want kept. Not for sensitive details.",
            saying,
            ("text",),
            run=remember,
        ),
        Tool(
            "recall",
            "Search long-term memory. Use it when past details or the owner's preferences may matter.",
            {"query": (STRING, "Words to look for.")},
            ("query",),
            run=recall,
        ),
        Tool(
            "propose_remember_sensitive",
            "Ask the owner to approve saving a sensitive detail.",
            saying,
            ("text",),
            propose=plan_sensitive,
            always_asks=True,
            owner_only=True,
        ),
        Tool(
            "propose_forget",
            "Ask the owner to approve deleting a memory.",
            {"id": (INTEGER, "The memory's id, from recall.")},
            ("id",),
            propose=plan_forget,
            always_asks=True,
            owner_only=True,
        ),
    ]
