"""The Wheelix mentor's course: one long class that carries on across many sessions.

What to teach lives in `prompts/mentor/wheelix.md`, the owner's own words, read fresh at
each start. This file adds only how the app works: the spoken first line, how progress is
marked so it can be shown and kept, and a list of real open-source projects to teach from.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path

from ..professor import renderer
from ..professor.llm_router import ProfessorModel, TeachingError
from ..professor.professor_mode import ROOT

log = logging.getLogger(__name__)

PROMPT = ROOT / "prompts" / "mentor" / "wheelix.md"
SAVED = ROOT / "sessions" / "mentor" / "wheelix.json"
MODEL = "gemini-3.8-flash"
SPARE_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.1-flash-lite")
MAX_HISTORY_TURNS = 16
STATES = ("assessing", "teaching", "checking", "passed")

APP_RULES = """\
How this app works, which you must follow:
- Replies appear in a notes window, and the student answers by typing or speaking.
- The FIRST line of every reply is one short sentence in plain words, which is read aloud. No symbols,
  equations, code or tables in it. Everything after it is only shown on screen.
- Write equations in LaTeX between $...$ or $$...$$. Write code and terminal commands in fenced code
  blocks with a language name. Draw diagrams and coordinate frames as text inside a code block, because
  the notes window cannot draw Mermaid.
- Teach one lesson over several replies, covering a few parts of the lesson structure each time, and end
  each reply by saying what the student should do or answer next. Never put a whole lesson in one reply.
- You cannot run commands, see the screen or open files. Only what the student sends reaches you, so ask
  for terminal output, a screenshot or a measurement when you need evidence.
- The very last line of every reply must be exactly:
  PROGRESS: level <number 0 to 12> | <current lesson title> | <assessing, teaching, checking or passed>
  Use passed only after the student has met the lesson's completion criteria.

Real open-source resources to build the course on. Recommend them where they genuinely help, explain why,
and always tell the student to check the current version and its compatibility before installing.
Never invent a repository, paper, version or result that is not listed here or that you are unsure of.
- ROS 2 documentation for Jazzy: docs.ros.org/en/jazzy (source: github.com/ros2/ros2_documentation).
  REP 2000 lists the supported platforms for each ROS 2 release.
- Gazebo's own documentation lists which Gazebo release pairs with each ROS 2 release (gazebosim.org/docs),
  and github.com/gazebosim/ros_gz connects them.
- SLAM Toolbox: github.com/SteveMacenski/slam_toolbox, and the paper "SLAM Toolbox: SLAM for the dynamic
  world" (Macenski and Jambrecic, Journal of Open Source Software, 2021).
- Nav2: github.com/ros-navigation/navigation2 and docs.nav2.org, including its first-time robot setup guide.
- robot_localization: github.com/cra-ros-pkg/robot_localization, for EKF sensor fusion.
- ros2_control and ros2_controllers (diff_drive_controller): github.com/ros-controls.
- TurtleBot3 and its simulations: github.com/ROBOTIS-GIT, a well-documented differential-drive reference.
- PythonRobotics: github.com/AtsushiSakai/PythonRobotics, small Python versions of EKF localization,
  particle filters, ICP scan matching, graph SLAM and path planning, good for learning the mathematics.
- Cartographer (github.com/cartographer-project/cartographer_ros), only as a comparison to SLAM Toolbox.
- Books: Thrun, Burgard and Fox, "Probabilistic Robotics"; Siegwart, Nourbakhsh and Scaramuzza,
  "Introduction to Autonomous Mobile Robots"; Lynch and Park, "Modern Robotics".
- Grisetti, Kummerle, Stachniss and Burgard, "A Tutorial on Graph-Based SLAM" (2010).
"""

FIRST_SESSION = (
    "This is our first session. Follow section 13 of your instructions: begin with a structured assessment of "
    "my current knowledge and my development environment, asking a few questions at a time, before Lesson 1. "
    "Here is what APLHA measured on my computer just now:\n\n{environment}"
)
WELCOME_BACK = "Welcome back. We were on level {level}, {lesson}."
_SUMMARY_RULE = (
    "Summarise this part of a robotics course in under 200 words, for the mentor's own memory. Keep what was "
    "taught, the student's answers and mistakes, decisions made about Wheelix, and anything the student must "
    "still do. Output only the summary."
)
_PROGRESS = re.compile(
    r"^[ \t*_`]*PROGRESS:[ \t]*level[ \t]*(\d{1,2})[ \t]*\|[ \t]*(.+?)[ \t]*\|[ \t]*([a-z]+)[ \t*_`.]*$",
    re.IGNORECASE | re.MULTILINE,
)


_ENTER = re.compile(
    r"^(please\s+)?((start|open|resume|begin)\s+(the\s+|my\s+)?)?(wheelix|robotics|slam)\s+(class|mentor|lesson|course)\b",
    re.IGNORECASE,
)


def is_enter(text: str) -> bool:
    return _ENTER.search(text.strip(" .!?,")) is not None


class MentorSetupError(Exception):
    """The prompt file is missing or a saved course is damaged. The message is shown to the owner."""


def load_prompt(path: Path | None = None) -> str:
    """Read fresh every time, so editing the file changes the next session with no code change."""
    path = path or PROMPT
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise MentorSetupError(f"The mentor's prompt file is missing: {path.name}") from error


def take_progress(text: str) -> tuple[str, dict | None]:
    """Removes every progress line, and returns the last one that makes sense."""
    found = None
    for level, lesson, state in _PROGRESS.findall(text):
        if 0 <= int(level) <= 12 and state.lower() in STATES:
            found = {"level": int(level), "lesson": lesson.strip()[:200], "state": state.lower()}
    return _PROGRESS.sub("", text).strip(), found


class MentorCourse:
    def __init__(self, prompt: str, model: ProfessorModel):
        self._prompt = prompt
        self._model = model
        self.history: list[tuple[str, str]] = []
        self.summary = ""
        self.transcript: list[str] = []
        self.progress = {"level": 0, "lesson": "Assessment", "state": "assessing"}
        self.passed: list[str] = []
        self.started = date.today().isoformat()

    @property
    def is_new(self) -> bool:
        return not self.history and not self.summary

    @property
    def model_name(self) -> str:
        return self._model.model

    def _system(self) -> str:
        parts = [self._prompt, APP_RULES, f"Today is {date.today().isoformat()}. The course began on {self.started}."]
        if self.passed:
            parts.append("Lessons the student has passed: " + "; ".join(self.passed[-40:]))
        if self.summary:
            parts.append(f"Earlier in this course:\n{self.summary}")
        return "\n\n".join(parts)

    def begin(self, environment: str) -> renderer.Reply:
        return self.respond(FIRST_SESSION.format(environment=environment), shown=False)

    def respond(self, text: str, shown: bool = True) -> renderer.Reply:
        if shown:
            self.transcript.append(f"**You:** {text}")
        answer = self._model.ask(self._system(), self.history, text)
        answer, progress = take_progress(answer)
        if progress is not None:
            self._note_progress(progress)
        else:
            log.warning("A mentor reply came with no progress line")
        self.history += [("user", text), ("model", answer)]
        if len(self.history) > 2 * MAX_HISTORY_TURNS:
            self._fold_old_turns()
        reply = renderer.split(answer)
        self.transcript.append(reply.screen)
        return reply

    def _note_progress(self, progress: dict) -> None:
        if progress["state"] == "passed" and progress["lesson"] not in self.passed:
            self.passed.append(progress["lesson"])
        self.progress = progress

    def _fold_old_turns(self) -> None:
        """Half the turns become a running summary. If that fails, the course carries on without it."""
        cut = MAX_HISTORY_TURNS // 2 * 2
        old, self.history = self.history[:cut], self.history[cut:]
        words = "\n\n".join(said for _, said in old)
        try:
            self.summary = self._model.ask(_SUMMARY_RULE, [], f"{self.summary}\n\n{words}".strip())
        except TeachingError as error:
            log.warning("The course summary was not updated: %s", error)

    def welcome(self) -> renderer.Reply:
        words = WELCOME_BACK.format(level=self.progress["level"], lesson=self.progress["lesson"])
        return renderer.Reply(words, "\n\n---\n\n".join(self.transcript) or words)

    def to_saved(self) -> dict:
        return {
            "history": [list(turn) for turn in self.history],
            "summary": self.summary,
            "transcript": self.transcript,
            "progress": self.progress,
            "passed": self.passed,
            "started": self.started,
        }

    @classmethod
    def restored(cls, saved: dict, prompt: str, model: ProfessorModel) -> "MentorCourse":
        """The prompt comes fresh from its file, so edits apply to a course already under way."""
        course = cls(prompt, model)
        try:
            course.history = [(role, said) for role, said in saved.get("history", [])]
            course.summary = str(saved.get("summary", ""))
            course.transcript = [str(item) for item in saved.get("transcript", [])]
            course.progress = dict(saved.get("progress", course.progress))
            course.passed = [str(item) for item in saved.get("passed", [])]
            course.started = str(saved.get("started", course.started))
        except (TypeError, ValueError) as error:
            raise MentorSetupError("The saved course could not be read.") from error
        return course


def save(course: MentorCourse, path: Path | None = None) -> None:
    """Written beside the real file first, so a crash cannot leave half a course."""
    path = path or SAVED
    path.parent.mkdir(parents=True, exist_ok=True)
    waiting = path.with_suffix(".tmp")
    waiting.write_text(json.dumps(course.to_saved(), ensure_ascii=False), encoding="utf-8")
    os.replace(waiting, path)


def load_saved(path: Path | None = None) -> dict | None:
    path = path or SAVED
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        raise MentorSetupError("The saved course could not be read.") from error
    if not isinstance(data, dict):
        raise MentorSetupError("The saved course could not be read.")
    return data
