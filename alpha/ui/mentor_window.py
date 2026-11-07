"""The Wheelix mentor window: the notes, the controls and the course's progress."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget

from ..mentor import environment
from ..mentor import mentor
from ..mentor.mentor import MentorCourse, MentorSetupError
from ..professor import math_render
from ..professor import professor_mode as professor
from ..professor.llm_router import ProfessorModel, TeachingError
from .professor_window import Notes
from .style import dark_title_bar

log = logging.getLogger(__name__)

CONTROLS = ("Continue", "Simpler", "Example", "Check my PC")
CHECK_WORDS = ("check my pc", "check my computer", "check my laptop")
MEASURED = "Here is what APLHA measured on my computer just now:\n\n{environment}"


class MentorWindow(QDialog):
    _answered = Signal(object, object)
    _waiting = Signal(str)

    def __init__(self, conversation, parent=None, prompt_path=None, saved_path=None, make_model=ProfessorModel,
                 measure=environment.measure):
        super().__init__(parent)
        self.setWindowTitle("Wheelix mentor")
        self.resize(900, 660)
        self._conversation = conversation
        self._prompt_path = prompt_path
        self._saved_path = saved_path
        self._make_model = make_model
        self._measure = measure
        self._course: MentorCourse | None = None
        self._busy = False
        self._notes = ""

        heading = QLabel("Wheelix mentor")
        heading.setObjectName("heading")
        self.progress = QLabel()
        self.progress.setObjectName("detail")
        self.status = QLabel()
        self.status.setObjectName("detail")
        self.status.setWordWrap(True)
        self.notes = Notes()
        self.notes.setOpenExternalLinks(False)
        self.notes.setOpenLinks(False)
        self.input = QLineEdit()
        self.input.setPlaceholderText("Answer, ask anything, or say continue")
        self.input.returnPressed.connect(self._send_typed)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_typed)
        self.control_area = QWidget()
        row = QHBoxLayout(self.control_area)
        row.setContentsMargins(0, 0, 0, 0)
        self.controls: dict[str, QPushButton] = {}
        for word in CONTROLS:
            button = QPushButton(word)
            button.clicked.connect(lambda _checked=False, said=word: self.handle(said))
            row.addWidget(button)
            self.controls[word] = button
        self.end_button = QPushButton("End class")
        self.end_button.clicked.connect(self.end_class)
        for button in (*self.controls.values(), self.send_button, self.end_button):
            # Otherwise pressing Enter in the typing box would also press a button.
            button.setAutoDefault(False)

        controls = QHBoxLayout()
        controls.addWidget(self.control_area)
        controls.addWidget(self.end_button)
        controls.addStretch(1)
        typing = QHBoxLayout()
        typing.addWidget(self.input, 1)
        typing.addWidget(self.send_button)
        top = QHBoxLayout()
        top.addWidget(heading)
        top.addStretch(1)
        top.addWidget(self.progress)
        layout = QVBoxLayout(self)
        layout.addLayout(top)
        layout.addWidget(self.status)
        layout.addWidget(self.notes, 1)
        layout.addLayout(controls)
        layout.addLayout(typing)
        self._answered.connect(self._show_answer)
        self._waiting.connect(self.status.setText)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    @property
    def in_class(self) -> bool:
        return self._course is not None

    def closeEvent(self, event) -> None:
        self.end_class()
        super().closeEvent(event)

    def reject(self) -> None:
        """Esc stops the voice, as everywhere else in APLHA, and leaves the class open."""
        self._conversation.interrupt()

    def start_class(self) -> None:
        if self.in_class:
            self.raise_()
            self.activateWindow()
            return
        try:
            prompt = mentor.load_prompt(self._prompt_path)
            saved = mentor.load_saved(self._saved_path)
        except MentorSetupError as error:
            self._conversation.notice.emit(str(error))
            return
        model = self._make_model(mentor.MODEL, on_wait=self._waiting.emit, spare=mentor.SPARE_MODELS)
        self._course = MentorCourse.restored(saved, prompt, model) if saved else MentorCourse(prompt, model)
        self._conversation.set_class(True)
        self._notes = ""
        self.notes.pictures.clear()
        self.notes.clear()
        self._show_progress()
        self.show()
        self.raise_()
        course = self._course
        if course.is_new:
            self.status.setText("Measuring your computer, then starting the first session.")
            self._run(lambda: course.begin(self._measure()))
        else:
            self._run(course.welcome)
        log.info("The Wheelix mentor started")

    def end_class(self) -> None:
        if not self.in_class:
            return
        self._save()
        self._course = None
        self._conversation.set_class(False)
        self.hide()
        log.info("The Wheelix mentor ended")

    def _send_typed(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.handle(text)

    def handle(self, text: str) -> None:
        if professor.is_exit(text):
            self.end_class()
            return
        if not self.in_class or self._busy:
            return
        course = self._course
        if " ".join(text.lower().split()).strip(" .!?") in CHECK_WORDS:
            self._add_note(math_render.Prepared("**You:** Check my PC"))
            self.status.setText("Measuring your computer.")
            self._run(lambda: course.respond(MEASURED.format(environment=self._measure()), shown=False))
            return
        self._add_note(math_render.Prepared(f"**You:** {text}"))
        self._run(lambda: course.respond(text))

    def _save(self) -> None:
        if self._course is not None and not self._course.is_new:
            try:
                mentor.save(self._course, self._saved_path)
            except OSError as error:
                self.status.setText(f"The course could not be saved ({type(error).__name__}).")

    def _run(self, work) -> None:
        self._set_busy(True)
        if not self.status.text():
            self.status.setText("Working. This can take a minute or two.")

        def tell(answer, problem) -> None:
            try:
                self._answered.emit(answer, problem)
            except RuntimeError:
                log.info("A mentor answer arrived after its window had gone")

        def background():
            try:
                reply = work()
                tell((reply, math_render.prepare(reply.screen)), None)
            except (TeachingError, MentorSetupError) as error:
                tell(None, str(error))
            except Exception as error:
                log.exception("The Wheelix mentor failed")
                tell(None, f"That failed ({type(error).__name__}).")

        threading.Thread(target=background, name="alpha-mentor", daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.control_area.setEnabled(not busy)
        self.send_button.setEnabled(not busy)

    def _show_answer(self, answer, problem) -> None:
        self._set_busy(False)
        if self._course is None:
            return
        if problem:
            self.status.setText(problem)
            return
        reply, prepared = answer
        self._add_note(prepared)
        self._conversation.say_aloud(reply.spoken)
        self._show_progress()
        changed = self._course.model_name != mentor.MODEL
        self.status.setText(f"Using {self._course.model_name}." if changed else "")
        self._save()

    def _show_progress(self) -> None:
        progress = self._course.progress
        self.progress.setText(f"Level {progress['level']}  ·  {progress['lesson']}  ·  {progress['state']}")

    def _add_note(self, prepared: math_render.Prepared) -> None:
        """The view stays at the start of the new part, not at its end."""
        start_of_new = int(self.notes.document().size().height()) if self._notes else 0
        self.notes.pictures.update(prepared.pictures)
        self._notes = f"{self._notes}\n\n---\n\n{prepared.markdown}" if self._notes else prepared.markdown
        self.notes.show_notes(self._notes)
        self.notes.verticalScrollBar().setValue(start_of_new)
