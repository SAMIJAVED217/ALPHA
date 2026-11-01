"""The class itself: which course, which prompts, and which slides come next.

No exam or course details live in this code. They are in `prompts/professor`, which is
read again at the start of each class, and `config/professor.json`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from . import renderer, slide_loader
from .llm_router import ProfessorModel, SlidesRefused

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
PROMPTS = ROOT / "prompts" / "professor"
CONFIG = ROOT / "config" / "professor.json"
SMALLEST_CHUNK = 5
LARGEST_CHUNK = 8
GOALS = {"exam": "exam prep", "review": "quick review", "learn": "learn new material"}
_ENTER = re.compile(r"^(please\s+)?(professor mode|start (the )?class|teach me)\b", re.IGNORECASE)
_EXIT = re.compile(r"^(please\s+)?(exit professor mode|end (the )?class)\b", re.IGNORECASE)
_FOR_APLHA = re.compile(r"^\W*(hey\s+)?(alpha|aplha)\b(?!\s+(particles?|decay|rays?|radiation)\b)", re.IGNORECASE)
_NEXT_WORDS = ("next", "next slides", "continue")
_QUIZ_WORDS = ("quiz me", "quiz")
_STOP_QUIZ_WORDS = ("stop quiz", "end quiz", "stop the quiz", "end the quiz")
_CONTROL_WORDS = ("simpler", "deeper", "example", "why", "summary")
_RESULT = re.compile(r"^[ \t*_`]*RESULT:[ \t]*(correct|wrong)[ \t*_`.]*$", re.IGNORECASE | re.MULTILINE)
# How to mark a grade, so the app can keep the score. What to teach stays in the prompt files.
_NO_SCORE = "Do not state a score or count questions, because the app keeps the score."
_GRADE_FORMAT = (
    f"After grading, ask the next question. {_NO_SCORE} "
    "As the very last line, write only RESULT: correct or RESULT: wrong for this answer. "
    "A partly right answer is wrong."
)
_END_OF_LECTURE = "That was the last slide. Now give the end-of-lecture material from your slide-by-slide protocol."
_COMMON_WORDS = {"intro", "introduction", "to", "the", "in", "of", "for", "and", "semiconductor", "industry", "course", "class"}


class ProfessorSetupError(Exception):
    """A prompt or config file is missing or broken. The message is shown to the owner."""


def is_enter(text: str) -> bool:
    return _ENTER.search(text.strip(" .!?,")) is not None


def is_exit(text: str) -> bool:
    return _EXIT.search(text.strip(" .!?,")) is not None


def is_for_aplha(text: str) -> bool:
    """During class, a message that starts with APLHA's name is a normal command."""
    return _FOR_APLHA.search(text) is not None


def _word(text: str) -> str:
    return " ".join(text.lower().split()).strip(" .!?,")


@dataclass(frozen=True)
class Course:
    id: str
    name: str
    folder: str


@dataclass(frozen=True)
class ProfessorConfig:
    gemini_model: str
    chunk_pages: int
    render_dpi: int
    max_history_turns: int
    courses: tuple[Course, ...]


def load_config(path: Path | None = None) -> ProfessorConfig:
    path = path or CONFIG
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ProfessorConfig(
            gemini_model=raw["gemini_model"],
            chunk_pages=max(SMALLEST_CHUNK, min(LARGEST_CHUNK, int(raw["chunk_pages"]))),
            render_dpi=int(raw["render_dpi"]),
            max_history_turns=int(raw["max_history_turns"]),
            courses=tuple(Course(item["id"], item["name"], item["folder"]) for item in raw["courses"]),
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ProfessorSetupError(f"{path.name} could not be read ({type(error).__name__}).") from error


def build_system_prompt(course_id: str, root: Path | None = None) -> str:
    """Exactly two files, read fresh, so editing one changes the next class with no code change."""
    root = root or PROMPTS
    core, course = root / "core.md", root / "courses" / f"{course_id}.md"
    try:
        prompt = f"{core.read_text(encoding='utf-8').strip()}\n\n{course.read_text(encoding='utf-8').strip()}"
    except OSError as error:
        raise ProfessorSetupError(f"A prompt file is missing: {Path(error.filename or '').name}") from error
    log.info("Professor Mode loaded prompts: %s, %s", core.name, course.name)
    return prompt


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z]+", text.lower())) - _COMMON_WORDS


def find_course(text: str, courses: tuple[Course, ...]) -> Course | None:
    """The clearest match for spoken words, or nothing if two courses fit equally well."""
    said = _words(text)
    ranked = []
    for course in courses:
        wanted = _words(f"{course.name} {course.id.replace('_', ' ')}")
        hits = len(said & wanted)
        if hits:
            ranked.append((hits, hits / len(wanted), course))
    ranked.sort(key=lambda row: row[:2], reverse=True)
    if not ranked or (len(ranked) > 1 and ranked[0][:2] == ranked[1][:2]):
        return None
    return ranked[0][2]


def find_goal(text: str) -> str:
    lowered = text.lower()
    return next((goal for word, goal in GOALS.items() if word in lowered), GOALS["learn"])


class Lesson:
    """The page pointer moves only after Gemini has really answered, so no slide is ever lost."""

    def __init__(self, course: Course, system: str, lecture: str, goal: str, slides: list[slide_loader.Slide],
                 model: ProfessorModel, config: ProfessorConfig):
        self.course = course
        self.lecture = lecture
        self.goal = goal
        self.pointer = 0
        self.closed = False
        self.quizzing = False
        self.correct = 0
        self.graded = 0
        self._system = system
        self._slides = slides
        self._model = model
        self._config = config
        self._history: list[tuple[str, str]] = []

    @property
    def total(self) -> int:
        return len(self._slides)

    @property
    def finished(self) -> bool:
        return self.pointer >= self.total

    def _remember(self, asked: str, answer: str) -> None:
        self._history += [("user", asked), ("model", answer)]
        self._history = self._history[-2 * self._config.max_history_turns:]

    @property
    def needs_closing(self) -> bool:
        return self.finished and not self.closed

    def respond(self, text: str) -> renderer.Reply:
        """Every button and spoken word comes here. Anything else in a quiz is an answer to grade."""
        word = _word(text)
        if word in _NEXT_WORDS:
            self.quizzing = False
            return self.teach_next()
        if word in _QUIZ_WORDS:
            self.quizzing = True
            return self.ask(f"quiz me\n\n{_NO_SCORE}")
        if self.quizzing and word in _STOP_QUIZ_WORDS:
            self.quizzing = False
            words = f"Quiz stopped at {self.correct} out of {self.graded}."
            return renderer.Reply(words, words)
        if self.quizzing and word not in _CONTROL_WORDS:
            return self._grade(text)
        return self.ask(text)

    def _grade(self, answer: str) -> renderer.Reply:
        asked = f"My answer: {answer}\n\n{_GRADE_FORMAT}"
        reply = self._model.ask(self._system, self._history, asked)
        grades = _RESULT.findall(reply)
        if grades:
            self.graded += 1
            self.correct += grades[-1].lower() == "correct"
        else:
            log.warning("A quiz reply came with no grade, so it was not scored")
        reply = _RESULT.sub("", reply).strip()
        self._remember(asked, reply)
        return renderer.split(reply)

    def _close_lecture(self) -> renderer.Reply:
        answer = self._model.ask(self._system, self._history, _END_OF_LECTURE)
        self._remember(_END_OF_LECTURE, answer)
        self.closed = True
        return renderer.split(answer)

    def teach_next(self) -> renderer.Reply:
        if self.closed:
            return renderer.Reply("That was the last slide of this lecture.", "That was the last slide of this lecture.")
        if self.finished:
            return self._close_lecture()
        first, last = self.pointer + 1, min(self.pointer + self._config.chunk_pages, self.total)
        chosen = self._slides[self.pointer:last]
        instruction = (
            f"Lecture: {self.lecture}. Goal: {self.goal}. Teach slides {first}–{last} using the slide-by-slide "
            f"protocol. The attached pages are slides {first} to {last} in order. Total slides: {self.total}."
        )
        try:
            answer = self._model.ask(self._system, self._history, instruction, slide_loader.as_files(chosen))
        except SlidesRefused:
            log.info("Sending slides %s to %s as pictures instead", first, last)
            pictures = slide_loader.as_pictures(chosen, self._config.render_dpi)
            answer = self._model.ask(self._system, self._history, instruction, pictures)
        self._remember(instruction, answer)
        self.pointer = last
        return renderer.split(answer)

    def ask(self, text: str) -> renderer.Reply:
        """Words only. The slides already taught are in the history, and the pointer stays put."""
        answer = self._model.ask(self._system, self._history, text)
        self._remember(text, answer)
        return renderer.split(answer)
