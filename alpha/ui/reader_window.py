"""The translated reader: a PDF page by page, or whatever is on the screen."""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from .. import languages, page_reader, settings as settings_store
from ..professor import slide_loader
from ..professor.llm_router import SlidesRefused, TeachingError
from ..voice.conversation import take_picture
from .style import dark_title_bar

log = logging.getLogger(__name__)

CAPTURE_DELAY_MS = 600
READING_PICTURE = "reading"


class ReaderWindow(QDialog):
    _translated = Signal(int, str)
    _sound_ready = Signal(int, bytes)
    _finished_page = Signal(int, str)
    _captured = Signal(int, bytes, str)
    _waiting = Signal(str)

    def __init__(self, conversation, settings, parent=None, reader=None, capture=None, render_dpi=150):
        super().__init__(parent)
        self.setWindowTitle("Translated reader")
        self.resize(760, 560)
        self._conversation = conversation
        self._settings = settings
        self._reader = reader or page_reader.PageReader(on_wait=self._waiting.emit)
        self._capture = capture or (lambda: take_picture(READING_PICTURE))
        self._render_dpi = render_dpi
        self._slides: list[slide_loader.Slide] = []
        self._page = 0
        self._run = 0
        self._page_ready = False
        self._reading = False
        self._hidden_state = None

        heading = QLabel("Translated reader")
        heading.setObjectName("heading")
        self.status = QLabel("Choose a PDF, or read what is on your screen.")
        self.status.setObjectName("detail")
        self.status.setWordWrap(True)
        self.language = QComboBox()
        for code, name in languages.INTERPRETER_LANGUAGES.items():
            self.language.addItem(name, code)
        self.language.setCurrentIndex(max(self.language.findData(settings.translate_to), 0))
        self.pdf_button = self._button("Choose PDF", self.choose_pdf)
        self.screen_button = self._button("Read my screen", self.read_screen)
        self.back_button = self._button("Previous page", lambda: self.go_to(self._page - 1))
        self.next_button = self._button("Next page", lambda: self.go_to(self._page + 1))
        self.stop_button = self._button("Stop", self.stop)
        self.keep_going = QCheckBox("Carry on to the next page")
        self.keep_going.setChecked(True)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setPlaceholderText("The translation appears here while it is read aloud")

        choices = QHBoxLayout()
        choices.addWidget(QLabel("Read in"))
        choices.addWidget(self.language, 1)
        choices.addWidget(self.pdf_button)
        choices.addWidget(self.screen_button)
        pages = QHBoxLayout()
        for widget in (self.back_button, self.next_button, self.stop_button, self.keep_going):
            pages.addWidget(widget)
        pages.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(heading)
        layout.addWidget(self.status)
        layout.addLayout(choices)
        layout.addWidget(self.text, 1)
        layout.addLayout(pages)

        self._translated.connect(self._show_translation)
        self._sound_ready.connect(self._play)
        self._finished_page.connect(self._page_finished)
        self._captured.connect(self._picture_taken)
        self._waiting.connect(self.status.setText)
        conversation.speech_finished.connect(self._speech_ended)
        conversation.reading_interrupted.connect(self.stop)
        self._show_buttons()

    def _button(self, words: str, action) -> QPushButton:
        button = QPushButton(words)
        button.setAutoDefault(False)
        button.clicked.connect(lambda _checked=False: action())
        return button

    def showEvent(self, event) -> None:
        super().showEvent(event)
        dark_title_bar(self)

    def closeEvent(self, event) -> None:
        self.stop()
        super().closeEvent(event)

    def reject(self) -> None:
        self.stop()

    def open_with(self, target: str, source: str) -> None:
        self.show()
        self.raise_()
        if target:
            self.language.setCurrentIndex(max(self.language.findData(target), 0))
        if source == "screen":
            self.read_screen()
        elif source == "pdf":
            self.choose_pdf()

    def _language_name(self) -> str:
        self._settings.translate_to = self.language.currentData()
        settings_store.save(self._settings)
        return self.language.currentText()

    def _show_buttons(self) -> None:
        self.back_button.setEnabled(self._page > 0)
        self.next_button.setEnabled(self._page + 1 < len(self._slides))

    def _start_reading(self) -> int:
        """Anything still arriving for an older page has an old number, so it is dropped."""
        self._run += 1
        self._page_ready = False
        if not self._reading:
            self._reading = True
            self._conversation.set_reading(True)
        self._conversation.stop_speech()
        self.text.clear()
        return self._run

    def stop(self) -> None:
        self._run += 1
        self._page_ready = False
        if self._reading:
            self._reading = False
            self._conversation.stop_speech()
            self._conversation.set_reading(False)
            self.status.setText("Stopped.")

    def choose_pdf(self) -> None:
        names, _ = QFileDialog.getOpenFileNames(self, "Choose a PDF", "", "Documents (*.pdf *.png *.jpg *.jpeg)")
        if not names:
            return
        try:
            self._slides = slide_loader.load(names)
        except slide_loader.SlideError as error:
            self.status.setText(str(error))
            return
        self.go_to(0)

    def go_to(self, page: int) -> None:
        if 0 <= page < len(self._slides):
            self._page = page
            self._show_buttons()
            self._read(self._slides[page], f"Page {page + 1} of {len(self._slides)}")

    def _read(self, slide: slide_loader.Slide, label: str) -> None:
        language = self._language_name()
        run = self._start_reading()
        self.status.setText(f"{label}: translating. This can take a few minutes.")

        def files() -> list[tuple[bytes, str]]:
            return slide_loader.as_files([slide])

        def again() -> list[tuple[bytes, str]]:
            return slide_loader.as_pictures([slide], self._render_dpi)

        threading.Thread(target=self._translate_and_speak, args=(run, files, again, language, label), daemon=True).start()

    def read_screen(self) -> None:
        """APLHA steps aside for the picture, or it would read its own windows."""
        language = self._language_name()
        run = self._start_reading()
        self._slides = []
        self._show_buttons()
        parent = self.parentWidget()
        self._hidden_state = parent.windowState() if parent is not None else None
        self.hide()
        if parent is not None:
            parent.setWindowState(parent.windowState() | Qt.WindowState.WindowMinimized)
        QTimer.singleShot(CAPTURE_DELAY_MS, lambda: self._take_picture(run, language))

    def _take_picture(self, run: int, language: str) -> None:
        def work():
            try:
                picture, problem = self._capture(), ""
            except Exception as error:
                picture, problem = b"", f"The screen could not be captured ({type(error).__name__})."
            self._tell(self._captured, run, picture, problem or language)

        threading.Thread(target=work, name="alpha-reader-capture", daemon=True).start()

    def _picture_taken(self, run: int, picture: bytes, detail: str) -> None:
        parent = self.parentWidget()
        if parent is not None and self._hidden_state is not None:
            parent.setWindowState(self._hidden_state)
        self.show()
        self.raise_()
        if run != self._run:
            return
        if not picture:
            self.status.setText(detail)
            return
        self.status.setText("Your screen: translating. This can take a few minutes.")

        def files() -> list[tuple[bytes, str]]:
            return [(picture, "image/jpeg")]

        threading.Thread(target=self._translate_and_speak, args=(run, files, files, detail, "Your screen"), daemon=True).start()

    @staticmethod
    def _tell(signal, *values) -> None:
        try:
            signal.emit(*values)
        except (RuntimeError, TypeError):
            log.info("A reader result arrived after its window had gone")

    def _translate_and_speak(self, run: int, files, other_files, language: str, label: str) -> None:
        try:
            try:
                text = self._reader.translate(files(), language)
            except SlidesRefused:
                text = self._reader.translate(other_files(), language)
            if not text:
                self._tell(self._finished_page, run, f"{label} has no text to read.")
                return
            self._tell(self._translated, run, text)
            for piece in page_reader.pieces(text):
                if run != self._run:
                    return
                self._tell(self._sound_ready, run, self._reader.speak(piece))
            self._tell(self._finished_page, run, "")
        except (TeachingError, slide_loader.SlideError) as error:
            self._tell(self._finished_page, run, str(error))
        except Exception as error:
            log.exception("The reader failed")
            self._tell(self._finished_page, run, f"That failed ({type(error).__name__}).")

    def _show_translation(self, run: int, text: str) -> None:
        if run == self._run:
            self.text.setPlainText(text)
            self.status.setText("Reading aloud.")

    def _play(self, run: int, sound: bytes) -> None:
        if run == self._run:
            self._conversation.play_speech(sound)

    def _page_finished(self, run: int, problem: str) -> None:
        if run != self._run:
            return
        if problem:
            self.status.setText(problem)
            self.stop_reading_quietly()
            return
        self._page_ready = True
        self._conversation.finish_speech()

    def stop_reading_quietly(self) -> None:
        if self._reading:
            self._reading = False
            self._conversation.set_reading(False)

    def _speech_ended(self) -> None:
        """Only once the whole page has been spoken."""
        if not self._page_ready:
            return
        self._page_ready = False
        more = self._page + 1 < len(self._slides)
        if more and self.keep_going.isChecked():
            self.go_to(self._page + 1)
            return
        self.status.setText("Finished reading.")
        self.stop_reading_quietly()
