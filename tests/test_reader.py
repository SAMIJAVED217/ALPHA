"""The translated reader, with pretend Gemini models. Nothing here reads the real screen."""

import gc
import io
import threading
import wave

import numpy as np
import pytest
from pypdf import PdfWriter
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QFileDialog

from alpha import page_reader
from alpha.professor.llm_router import ProfessorModel, SlidesRefused, TeachingError
from alpha.settings import Settings
from alpha.tools.catalogue import build_toolbox
from alpha.tools.registry import ToolRefused
from alpha.ui.reader_window import ReaderWindow

from .test_interface import spin_until as wait_briefly


def spin_until(condition):
    return wait_briefly(condition, seconds=10)


class FakeReader:
    def __init__(self, text="First sentence. Second sentence."):
        self.text = text
        self.pages = []
        self.spoken = []
        self.error = None
        self.refuse_once = False

    def translate(self, files, language):
        self.pages.append((files, language))
        if self.refuse_once:
            self.refuse_once = False
            raise SlidesRefused("no")
        if self.error:
            raise self.error
        return self.text

    def speak(self, text):
        self.spoken.append(text)
        return b"\0\0" * 100


class FakeConversation(QObject):
    speech_finished = Signal()
    reading_interrupted = Signal()

    def __init__(self):
        super().__init__()
        self.played, self.reading, self.finished, self.stopped = [], [], 0, 0

    def set_reading(self, on):
        self.reading.append(on)

    def play_speech(self, sound):
        self.played.append(sound)

    def finish_speech(self):
        self.finished += 1

    def stop_speech(self):
        self.stopped += 1


def pdf_of(path, pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(200, 200)
    with open(path, "wb") as out:
        writer.write(out)
    return path


@pytest.fixture
def desk(qapp, tmp_path, monkeypatch):
    pdf = pdf_of(tmp_path / "lecture.pdf", 3)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a: ([str(pdf)], ""))
    conversation, reader = FakeConversation(), FakeReader()
    window = ReaderWindow(conversation, Settings(), reader=reader, capture=lambda: b"picture", render_dpi=72)
    yield window, conversation, reader
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def test_a_page_is_translated_shown_and_spoken_piece_by_piece(desk):
    window, conversation, reader = desk
    window.choose_pdf()
    assert spin_until(lambda: conversation.finished == 1)
    assert reader.pages[0][1] == "English"
    assert window.text.toPlainText() == "First sentence. Second sentence."
    assert conversation.played and conversation.reading == [True]


def test_the_next_page_follows_only_after_the_speech_ends(desk):
    window, conversation, reader = desk
    window.choose_pdf()
    assert spin_until(lambda: conversation.finished == 1)
    assert len(reader.pages) == 1
    conversation.speech_finished.emit()
    assert spin_until(lambda: len(reader.pages) == 2)
    assert window._page == 1


def test_speech_ending_between_pieces_does_not_turn_the_page(desk):
    window, conversation, reader = desk
    release = threading.Event()
    original = reader.speak
    reader.speak = lambda text: (release.wait(5), original(text))[1]
    window.choose_pdf()
    assert spin_until(lambda: window.text.toPlainText())
    conversation.speech_finished.emit()
    QCoreApplication.processEvents()
    release.set()
    assert spin_until(lambda: conversation.finished == 1)
    assert window._page == 0


def test_it_can_stay_on_one_page(desk):
    window, conversation, reader = desk
    window.keep_going.setChecked(False)
    window.choose_pdf()
    assert spin_until(lambda: conversation.finished == 1)
    conversation.speech_finished.emit()
    assert spin_until(lambda: "Finished" in window.status.text())
    assert len(reader.pages) == 1
    assert conversation.reading == [True, False]


def test_the_last_page_ends_and_gives_hands_free_back(desk):
    window, conversation, reader = desk
    window.choose_pdf()
    for page in range(3):
        assert spin_until(lambda: conversation.finished == page + 1)
        conversation.speech_finished.emit()
        QCoreApplication.processEvents()
    assert spin_until(lambda: "Finished" in window.status.text())
    assert conversation.reading == [True, False]


def test_stop_silences_it_and_late_results_are_dropped(desk):
    window, conversation, reader = desk
    release = threading.Event()
    original = reader.translate
    reader.translate = lambda files, language: (release.wait(5), original(files, language))[1]
    window.choose_pdf()
    window.stop_button.click()
    release.set()
    assert spin_until(lambda: reader.pages)
    QCoreApplication.processEvents()
    assert window.text.toPlainText() == "" and conversation.played == []
    assert conversation.reading == [True, False]


def test_esc_from_the_main_window_stops_it(desk):
    window, conversation, _ = desk
    window.choose_pdf()
    conversation.reading_interrupted.emit()
    assert conversation.reading == [True, False]


def test_the_close_button_closes_the_window(desk):
    window, _, _ = desk
    window.show()
    window.close()
    assert not window.isVisible()


def test_esc_stops_reading_but_leaves_the_window_open(desk):
    window, conversation, _ = desk
    window.show()
    window.choose_pdf()
    window.reject()
    assert window.isVisible() and conversation.reading == [True, False]


def test_page_buttons(desk):
    window, conversation, reader = desk
    window.keep_going.setChecked(False)
    window.choose_pdf()
    assert not window.back_button.isEnabled() and window.next_button.isEnabled()
    window.next_button.click()
    assert window._page == 1 and window.back_button.isEnabled()
    window.next_button.click()
    assert window._page == 2 and not window.next_button.isEnabled()


def test_a_pdf_gemini_refuses_is_sent_as_a_picture(desk):
    window, conversation, reader = desk
    reader.refuse_once = True
    window.choose_pdf()
    assert spin_until(lambda: len(reader.pages) == 2)
    assert reader.pages[0][0][0][1] == "application/pdf"
    assert reader.pages[1][0][0][1] == "image/png"


def test_a_failure_is_shown_and_listening_comes_back(desk):
    window, conversation, reader = desk
    reader.error = TeachingError("Gemini is still busy.")
    window.choose_pdf()
    assert spin_until(lambda: "busy" in window.status.text())
    assert conversation.reading == [True, False]


def test_a_page_with_no_text_says_so(desk):
    window, conversation, reader = desk
    reader.text = ""
    window.choose_pdf()
    assert spin_until(lambda: "no text" in window.status.text())
    assert conversation.played == []


def test_reading_the_screen_hides_aplha_then_translates_the_picture(desk):
    window, conversation, reader = desk
    window.show()
    window.read_screen()
    assert window.isHidden()
    assert spin_until(lambda: conversation.finished == 1)
    assert not window.isHidden()
    assert reader.pages[0][0] == [(b"picture", "image/jpeg")]


def test_a_failed_capture_is_reported(qapp):
    def broken():
        raise RuntimeError("no")

    conversation = FakeConversation()
    window = ReaderWindow(conversation, Settings(), reader=FakeReader(), capture=broken)
    try:
        window.read_screen()
        assert spin_until(lambda: "could not be captured" in window.status.text())
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()


def test_the_chosen_language_is_used_and_remembered(desk):
    window, conversation, reader = desk
    window.language.setCurrentIndex(window.language.findData("ur"))
    window.choose_pdf()
    assert spin_until(lambda: reader.pages)
    assert reader.pages[0][1] == "Urdu" and window._settings.translate_to == "ur"


# The translation and speech steps.


class Gemini:
    def __init__(self, text="ترجمہ"):
        self.calls = []
        self.text = text
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        sound = type("Sound", (), {"data": b"\1\0", "mime_type": "audio/L16;rate=24000"})()
        part = type("Part", (), {"inline_data": sound})()
        content = type("Content", (), {"parts": [part]})()
        return type("Reply", (), {"text": self.text, "candidates": [type("Candidate", (), {"content": content})()]})()


def reader_with(gemini):
    def make(name):
        return ProfessorModel(name, load_key=lambda: "k", make_client=lambda key: gemini)

    return page_reader.PageReader(make(page_reader.READER_MODEL), make(page_reader.VOICE_MODEL))


def test_the_rule_forbids_summaries_and_invention():
    gemini = Gemini()
    reader_with(gemini).translate([(b"x", "application/pdf")], "Urdu")
    rule = gemini.calls[0][2]["system_instruction"]
    assert "Urdu" in rule and "Do not summarise" in rule and "unreadable" in rule and "word for word" in rule


def test_a_page_with_no_text_is_empty():
    assert reader_with(Gemini(page_reader.NO_TEXT)).translate([(b"x", "application/pdf")], "Urdu") == ""


def test_speech_uses_the_speech_model():
    gemini = Gemini()
    assert reader_with(gemini).speak("hello") == b"\1\0"
    assert gemini.calls[0][0] == page_reader.VOICE_MODEL
    assert gemini.calls[0][2]["response_modalities"] == ["AUDIO"]


def test_pieces_keep_every_word_in_order():
    text = " ".join(f"Sentence number {index} is here." for index in range(200))
    found = page_reader.pieces(text)
    assert len(found) > 1 and all(len(piece) <= page_reader.LONGEST_PIECE for piece in found)
    assert " ".join(found) == text


def test_urdu_sentences_are_split_too():
    assert len(page_reader.pieces("پہلا جملہ۔ " * 200)) > 1


def test_one_huge_sentence_is_still_divided_without_loss():
    text = "x" * 2500
    assert "".join(page_reader.pieces(text)) == text


def wav_of(rate):
    out = io.BytesIO()
    with wave.open(out, "wb") as file:
        file.setnchannels(1)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(np.zeros(rate, np.int16).tobytes())
    return out.getvalue()


def test_a_wav_header_never_reaches_the_speakers():
    sound = page_reader.as_speaker_sound(wav_of(24_000), "audio/wav")
    assert len(sound) == 48_000 and b"RIFF" not in sound


def test_other_rates_are_converted():
    assert abs(len(page_reader.as_speaker_sound(wav_of(48_000), "audio/wav")) - 48_000) <= 4


def test_raw_sound_passes_through():
    assert page_reader.as_speaker_sound(b"\1\0\2\0", "audio/L16;rate=24000") == b"\1\0\2\0"


def test_the_voice_tool_checks_its_choices():
    tool = build_toolbox(None).get("open_translated_reader")
    assert tool.run({"language": "Urdu", "source": "screen"})["source"] == "screen"
    with pytest.raises(ToolRefused):
        tool.run({"language": "Klingon", "source": "pdf"})
    with pytest.raises(ToolRefused):
        tool.run({"language": "Urdu", "source": "camera"})
