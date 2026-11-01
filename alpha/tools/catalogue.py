"""The full set of tools offered to the model."""

from __future__ import annotations

from .computer import computer_tools
from .everyday import WebInfo, everyday_tools
from .files import file_tools
from .gmail import GmailClient, gmail_tools
from .grok import SecondAI, grok_tools
from .memory_tools import memory_tools
from .notion import NotionClient, notion_tools
from .registry import Tool, ToolBox, ToolRefused, UndoStack, text
from .watch_tools import watch_tools
from .. import profile
from ..reminders import ReminderBook
from ..voice.persona import FILE_TOOL
from ..watchdog import WatchBoard

QUIT_TOOL = "close_aplha"
VIEW_TOOL = "share_view"

_FILE_PARAMETERS = {
    "file_name": ("STRING", "Name only, no folders, ending in .txt, .md, .csv, .json or .log."),
    "content": ("STRING", "The full text of the file."),
}


def _no_plan(_arguments):
    raise NotImplementedError("File proposals go straight to the safety gate.")


def _update_about_me(arguments):
    field = str(arguments.get("field", ""))
    if field not in profile.FIELDS:
        raise ToolRefused(f"field must be one of: {', '.join(profile.FIELDS)}.")
    profile.update(field, text(arguments, "text", profile.LONGEST_ANSWER))
    return {"ok": True, "detail": f"Updated About me: {profile.FIELDS[field]}."}


def _closing(_arguments):
    return {"ok": True, "detail": "Closing APLHA."}


def _run_by_the_conversation(_arguments):
    raise NotImplementedError("Sharing a view needs the live session, so the conversation does it.")


def build_toolbox(computer, memory=None, notion=None, gmail=None, grok=None) -> ToolBox:
    file_tool = Tool(
        FILE_TOOL,
        "Create one new text file in APLHA's Created files folder.",
        _FILE_PARAMETERS,
        ("file_name", "content"),
        propose=_no_plan,
    )
    undo = UndoStack()
    on_computer = computer_tools(computer, undo)
    reader = next(tool for tool in on_computer if tool.name == "read_window")

    def read_window(title: str) -> list[str]:
        return [item["label"] for item in reader.run({"window": title})["untrusted_window_contents"]]

    board = WatchBoard(read_window)
    reminders = ReminderBook()
    return ToolBox(
        [
            file_tool,
            *on_computer,
            *watch_tools(board),
            *everyday_tools(reminders, WebInfo(), computer),
            *file_tools(computer, undo),
            Tool(QUIT_TOOL, "Close APLHA itself, only when the owner asks.", run=_closing),
            Tool(
                VIEW_TOOL,
                "Turn the owner's camera or screen sharing on or off, only when they ask you to look or stop.",
                {"source": ("STRING", "camera or screen."), "on": ("BOOLEAN", "True to start, false to stop.")},
                ("source", "on"),
                run=_run_by_the_conversation,
            ),
            Tool(
                "update_about_me",
                "Change one answer in the owner's About me, when they tell you to or share a lasting change.",
                {
                    "field": ("STRING", ", ".join(profile.FIELDS)),
                    "text": ("STRING", "The full new answer, keeping what still applies."),
                },
                ("field", "text"),
                run=_update_about_me,
            ),
            *memory_tools(memory, undo),
            *notion_tools(notion),
            *gmail_tools(gmail),
            *grok_tools(grok),
        ],
        undo,
        board,
        reminders,
        computer,
    )


def real_toolbox(memory) -> ToolBox:
    from .windows_computer import WindowsComputer

    return build_toolbox(WindowsComputer(), memory, NotionClient(), GmailClient(), SecondAI())


def declarations() -> list[dict]:
    """What the model is told about. Nothing here can run."""
    return build_toolbox(None).declarations()
