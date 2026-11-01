"""The Professor Mode window: choosing the class, the notes, and the controls."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Signal
from PySide6.QtGui import QImage, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..professor import professor_mode as professor
from ..professor import math_render, renderer, session_store, slide_loader
from ..professor.llm_router import ProfessorModel, TeachingError
from ..professor.professor_mode import Lesson, ProfessorSetupError
from .style import dark_title_bar

log = logging.getLogger(__name__)

CHOOSING_COURSE, GIVING_DETAILS, CHOOSING_SLIDES, TEACHING = "course", "details", "slides", "teaching"
SLIDE_FILTER = "Slides (*.pdf *.png *.jpg *.jpeg)"
CONTROLS = ("Next", "Simpler", "Deeper", "Example", "Why?", "Quiz me", "Summary", "Export")
RESUME_CHOICES = 3


class Notes(QTextBrowser):
    """Shows Markdown, with equations supplied as pictures from memory."""

    def __init__(self):
        super().__init__()
        self.pictures: dict[str, bytes] = {}

    def loadResource(self, kind, name):
        data = self.pictures.get(name.toString())
        if kind == QTextDocument.ResourceType.ImageResource and data:
            picture = QImage.fromData(data)
            picture.setDevicePixelRatio(math_render.SHARPNESS)
            return picture
        return super().loadResource(kind, name)


class ProfessorWindow(QDialog):
    _answered = Signal(object, object)
    _waiting = Signal(str)

    def __init__(self, conversation, parent=None, config_path=None, prompts=None, make_model=ProfessorModel,
                 sessions=None):
        super().__init__(parent)
        self.setWindowTitle("Professor Mode")
        self.resize(880, 640)
        self._conversation = conversation
        self._config_path = config_path
        self._prompts = prompts
        self._make_model = make_model
        self._sessions = sessions
        self._saved: list[session_store.SavedClass] = []
        self._stage = ""
        self._config = None
        self._course = None
        self._system = ""
        self._lecture = ""
        self._goal = ""
        self._lesson: Lesson | None = None
        self._busy = False
        self._notes = ""

        self.heading = QLabel("Professor Mode")
        self.heading.setObjectName("heading")
        self.status = QLabel()
        self.status.setObjectName("detail")
        self.status.setWordWrap(True)
        self.course_area = QWidget()
        self._course_grid = QGridLayout(self.course_area)
        self.notes = Notes()
        self.notes.setOpenExternalLinks(False)
        self.notes.setOpenLinks(False)
        self.input = QLineEdit()
        self.input.returnPressed.connect(self._send_typed)
        self.send_button = QPushButton("Send")
        self.send_button.clicked.connect(self._send_typed)
        self.choose_button = QPushButton("Choose slides")
        self.choose_button.clicked.connect(self.choose_slides)
        self.score = QLabel()
        self.score.setObjectName("detail")
        self.score.hide()
        self.control_area = QWidget()
        control_row = QHBoxLayout(self.control_area)
        control_row.setContentsMargins(0, 0, 0, 0)
        self.controls: dict[str, QPushButton] = {}
        for word in CONTROLS:
            button = QPushButton(word)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, said=word: self.handle(said))
            control_row.addWidget(button)
            self.controls[word] = button
        self.end_button = QPushButton("End class")
        self.end_button.clicked.connect(self.end_class)

        controls = QHBoxLayout()
        for widget in (self.choose_button, self.control_area, self.end_button):
            controls.addWidget(widget)
        controls.addStretch(1)
        controls.addWidget(self.score)
        for widget in (self.send_button, self.choose_button, self.end_button):
            # Otherwise pressing Enter in the typing box would also press a button.
            widget.setAutoDefault(False)
        typing = QHBoxLayout()
        typing.addWidget(self.input, 1)
        typing.addWidget(self.send_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.heading)
        layout.addWidget(self.status)
        layout.addWidget(self.course_area, 1)
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
        return bool(self._stage)

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
            self._config = professor.load_config(self._config_path)
        except ProfessorSetupError as error:
            self._conversation.notice.emit(str(error))
            return
        self._stage = CHOOSING_COURSE
        self._conversation.set_class(True)
        self._lesson, self._course, self._notes = None, None, ""
        self.notes.pictures.clear()
        self.heading.setText("Professor Mode")
        self.notes.clear()
        while self._course_grid.count():
            self._course_grid.takeAt(0).widget().deleteLater()
        self._saved = session_store.saved_classes(self._sessions)
        for row, saved in enumerate(self._saved[:RESUME_CHOICES]):
            button = QPushButton(f"Resume: {saved.label}".replace("&", "&&"))
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, chosen=saved: self.resume(chosen))
            self._course_grid.addWidget(button, row, 0, 1, 2)
        first_row = min(len(self._saved), RESUME_CHOICES)
        for index, course in enumerate(self._config.courses):
            button = QPushButton(course.name.replace("&", "&&"))  # A lone & would underline the next letter.
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, chosen=course: self.choose_course(chosen))
            self._course_grid.addWidget(button, first_row + index // 2, index % 2)
        self._course_grid.setRowStretch(first_row + len(self._config.courses), 1)
        self.course_area.show()
        self.notes.hide()
        self.choose_button.hide()
        self.control_area.hide()
        self.score.hide()
        self.input.setPlaceholderText("Or say or type the course")
        self._say("Which course shall we study?")
        self.show()
        self.raise_()

    def end_class(self) -> None:
        if not self.in_class:
            return
        self._save()
        self._stage = ""
        self._lesson = None
        self._conversation.set_class(False)
        self.hide()
        log.info("Professor Mode ended")

    def _say(self, words: str) -> None:
        self.status.setText(words)
        self._conversation.say_aloud(words)

    def _send_typed(self) -> None:
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.handle(text)

    def handle(self, text: str) -> None:
        if professor.is_exit(text):
            self.end_class()
        elif self._stage == CHOOSING_COURSE and professor.is_resume(text):
            if self._saved:
                self.resume(self._saved[0])
            else:
                self._say("There is no saved class yet. Which course shall we study?")
        elif self._stage == CHOOSING_COURSE:
            course = professor.find_course(text, self._config.courses)
            if course is None:
                self._say("I did not catch which course. Please pick one from the list.")
            else:
                self.choose_course(course)
        elif self._stage == GIVING_DETAILS:
            self._lecture, self._goal = text, professor.find_goal(text)
            self.choose_slides()
        elif self._stage == TEACHING and professor.is_export(text):
            self.export()
        elif self._stage == TEACHING and not self._busy:
            self._add_note(math_render.Prepared(f"**You:** {text}"))
            self._run(lambda: self._lesson.respond(text))

    def choose_course(self, course: professor.Course) -> None:
        try:
            self._system = professor.build_system_prompt(course.id, self._prompts)
        except ProfessorSetupError as error:
            self._say(str(error))
            return
        self._course = course
        self._stage = GIVING_DETAILS
        self.heading.setText(course.name)
        self.course_area.hide()
        self.input.setPlaceholderText("For example: week 9, ion implantation, exam prep")
        self._say("Which lecture is it, and is the goal to learn, exam prep or review?")
        self.input.setFocus()

    def choose_slides(self) -> None:
        self._stage = CHOOSING_SLIDES
        self.choose_button.show()
        self.status.setText("Choose the slides for this lecture.")
        names, _ = QFileDialog.getOpenFileNames(self, "Choose slides", self._course.folder, SLIDE_FILTER)
        if not names:
            return
        try:
            slides = slide_loader.load(names)
        except slide_loader.SlideError as error:
            self.status.setText(str(error))
            return
        if not slides:
            self.status.setText("Those files have no pages.")
            return
        model = self._make_model(self._config.gemini_model, on_wait=self._waiting.emit)
        self._lesson = Lesson(self._course, self._system, self._lecture, self._goal, slides, model, self._config,
                              professor.groq_followups(self._config), professor.groq_standby(self._config))
        self._show_teaching()
        self._run(self._lesson.teach_next)

    def _show_teaching(self) -> None:
        self._stage = TEACHING
        self.course_area.hide()
        self.choose_button.hide()
        self.control_area.show()
        self.notes.show()
        self.input.setPlaceholderText("Ask anything, or say next")

    def resume(self, saved: session_store.SavedClass) -> None:
        course = next((item for item in self._config.courses if item.id == saved.data["course_id"]), None)
        if course is None:
            self._say("That course is no longer in the config.")
            return
        try:
            system = professor.build_system_prompt(course.id, self._prompts)
            slides = slide_loader.load(saved.data["files"])
            model = self._make_model(self._config.gemini_model, on_wait=self._waiting.emit)
            self._lesson = Lesson.restored(saved.data, course, system, slides, model, self._config,
                                           professor.groq_followups(self._config), professor.groq_standby(self._config))
        except (ProfessorSetupError, slide_loader.SlideError) as error:
            self._say(f"That class could not be resumed. {error}")
            return
        self._course = course
        self._lecture = self._lesson.lecture
        self.heading.setText(course.name)
        self._show_teaching()
        words = f"Welcome back. We have covered {self._lesson.pointer} of {self._lesson.total} slides."
        notes = "\n\n---\n\n".join(self._lesson.transcript) or words
        self._run(lambda: renderer.Reply(words, notes))

    def _save(self) -> None:
        if self._lesson is not None:
            try:
                session_store.save(self._lesson, self._sessions)
            except OSError as error:
                self.status.setText(f"This class could not be saved ({type(error).__name__}).")

    def export(self) -> None:
        try:
            path = session_store.export_notes(self._lesson)
        except (ProfessorSetupError, OSError) as error:
            self.status.setText(f"The notes were not exported. {error}")
            return
        self.status.setText(f"Notes saved as {path.name} in the course folder.")

    def _run(self, work) -> None:
        self._set_busy(True)
        self.status.setText("Working. This can take a minute or two.")

        def tell(answer, problem) -> None:
            try:
                self._answered.emit(answer, problem)
            except RuntimeError:
                log.info("A class answer arrived after its window had gone")

        def background():
            try:
                reply = work()
                tell((reply, math_render.prepare(reply.screen)), None)
            except (TeachingError, slide_loader.SlideError) as error:
                tell(None, str(error))
            except Exception as error:
                log.exception("Professor Mode failed")
                tell(None, f"That failed ({type(error).__name__}).")

        threading.Thread(target=background, name="alpha-professor", daemon=True).start()

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.control_area.setEnabled(not busy)
        self.send_button.setEnabled(not busy)

    def _show_answer(self, answer, problem) -> None:
        self._set_busy(False)
        if self._lesson is None:
            return
        if problem:
            self.status.setText(problem)
            return
        reply, prepared = answer
        self._add_note(prepared)
        self._conversation.say_aloud(reply.spoken)
        self._show_progress()
        self._save()
        if self._lesson.needs_closing:
            self._run(self._lesson.teach_next)
            self.status.setText("Writing the summary and exam questions. This can take several minutes.")

    def _show_progress(self) -> None:
        self.status.setText(f"Taught {self._lesson.pointer} of {self._lesson.total} slides.")
        self.score.setText(f"Quiz score: {self._lesson.correct} / {self._lesson.graded}")
        self.score.setVisible(self._lesson.quizzing or self._lesson.graded > 0)

    def _add_note(self, prepared: math_render.Prepared) -> None:
        """The view stays at the start of the new part, not at its end."""
        start_of_new = int(self.notes.document().size().height()) if self._notes else 0
        self.notes.pictures.update(prepared.pictures)
        self._notes = f"{self._notes}\n\n---\n\n{prepared.markdown}" if self._notes else prepared.markdown
        self.notes.setMarkdown(self._notes)
        self.notes.verticalScrollBar().setValue(start_of_new)
