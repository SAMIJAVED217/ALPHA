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
from .. import languages, profile
from ..reminders import ReminderBook
from ..voice.persona import FILE_TOOL
from ..watchdog import WatchBoard

QUIT_TOOL = "close_aplha"
VIEW_TOOL = "share_view"
CLASS_TOOL = "start_professor_mode"
INTERPRET_TOOL = "start_live_translation"
READER_TOOL = "open_translated_reader"

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


def _opening_class(_arguments):
    return {"ok": True, "detail": "Opening Professor Mode."}


def _starting_translation(arguments):
    code = languages.interpreter_code(text(arguments, "language", 40))
    if code is None:
        raise ToolRefused("The live translation cannot speak that language.")
    return {"ok": True, "target": code, "detail": f"Starting live translation into {languages.INTERPRETER_LANGUAGES[code]}."}


def _opening_reader(arguments):
    code = languages.interpreter_code(text(arguments, "language", 40))
    source = str(arguments.get("source", "pdf")).strip().lower()
    if code is None or source not in ("pdf", "screen"):
        raise ToolRefused("Choose a language it can speak, and pdf or screen.")
    return {"ok": True, "target": code, "source": source, "detail": "Opening the translated reader."}


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
                INTERPRET_TOOL,
                "Start live interpreting, when the owner wants speech around them translated aloud as it is spoken.",
                {"language": ("STRING", "The language to speak the translation in, such as English or Urdu.")},
                ("language",),
                run=_starting_translation,
            ),
            Tool(
                READER_TOOL,
                "Read a PDF or what is on the screen aloud, translated word by word, when the owner asks.",
                {"language": ("STRING", "The language to read in."), "source": ("STRING", "pdf or screen.")},
                ("language", "source"),
                run=_opening_reader,
            ),
            Tool(CLASS_TOOL, "Open Professor Mode, when the owner says professor mode, start class or teach me.",
                 run=_opening_class),
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
