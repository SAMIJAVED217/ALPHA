"""Tools for telling APLHA what to watch. What is found goes straight to the owner, not the model."""

from __future__ import annotations

from ..watchdog import WatchBoard
from .registry import STRING, Tool, ToolRefused, text


def watch_tools(board: WatchBoard) -> list[Tool]:
    def watch(arguments):
        name = text(arguments, "name", 60)
        source = text(arguments, "source", 500)
        words = [word.strip() for word in text(arguments, "looking_for", 500).split(",") if word.strip()]
        if not words:
            raise ToolRefused("looking_for needs at least one word or phrase.")
        board.add(name, source, words)
        return {"ok": True, "detail": f"Watching {name} for: {', '.join(words)}. The first look learns what is there now."}

    def show(_arguments):
        items = [{"name": item.name, "source": item.source, "looking_for": item.looking_for, "problem": item.problem}
                 for item in board.watches.values()]
        return {"ok": True, "watches": items, "detail": f"Watching {len(items)} page(s)."}

    def stop(arguments):
        name = text(arguments, "name", 60)
        if not board.remove(name):
            raise ToolRefused(f"Nothing called {name} is being watched.")
        return {"ok": True, "detail": f"Stopped watching {name}."}

    return [
        Tool(
            "watch_page",
            "Keep checking a page, such as a university portal, and alert the owner to new lines they care about.",
            {
                "name": (STRING, "A short name, for example University portal."),
                "source": (STRING, "A public web address, or for a page that needs signing in, its open window title."),
                "looking_for": (STRING, "Words or phrases from the owner, separated by commas."),
            },
            ("name", "source", "looking_for"),
            run=watch,
        ),
        Tool("list_watches", "List the pages being watched.", run=show),
        Tool("stop_watching", "Stop watching a page.", {"name": (STRING, "Its name.")}, ("name",), run=stop),
    ]
