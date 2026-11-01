"""Professor Mode, with a pretend Gemini. The real prompt and config files are never read here."""

import gc
import json
import re
import threading

import pytest
from pypdf import PdfWriter
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QFileDialog

from alpha.professor import llm_router, renderer, slide_loader
from alpha.professor import professor_mode as professor
from alpha.professor.llm_router import ProfessorModel, SlidesRefused, TeachingError
from alpha.professor.professor_mode import Course, Lesson, ProfessorConfig, ProfessorSetupError
from alpha.professor.slide_loader import Slide, SlideError
from alpha.ui.professor_window import ProfessorWindow

from .test_interface import spin_until


class Gemini:
    """Records what it was given and answers like the prompt asks."""

    def __init__(self, failures=()):
        self.calls = []
        self.failures = list(failures)
        self.models = self

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        if self.failures:
            raise self.failures.pop(0)
        return type("Reply", (), {"text": "Here are the slides.\n### Slide\n| a | b |\n|---|---|\n$E = mc^2$"})()


class Failure(Exception):
    def __init__(self, code):
        super().__init__("failed")
        self.code = code


def model_with(gemini, waits=None, slept=None):
    return ProfessorModel(
        "test-model", load_key=lambda: "a-key", make_client=lambda key: gemini,
        on_wait=(waits if waits is not None else []).append, sleep=(slept if slept is not None else []).append,
    )


def config(chunk=6):
    return ProfessorConfig("test-model", chunk, 72, 10, COURSES)


COURSES = (
    Course("manufacturing_process", "Intro to Semiconductor Manufacturing Process", "D:\\a"),
    Course("equipment", "Intro to Semiconductor Equipment", "D:\\b"),
    Course("materials_components_equipment", "Intro to Semiconductor Materials, Components & Equipment", "D:\\c"),
    Course("control_engineering", "Control Engineering for Semiconductor Equipment", "D:\\d"),
    Course("cad", "Computer Aided Design", "D:\\e"),
)


@pytest.fixture
def prompts(tmp_path):
    root = tmp_path / "prompts"
    (root / "courses").mkdir(parents=True)
    (root / "core.md").write_text("CORE RULES", encoding="utf-8")
    for course in COURSES:
        (root / "courses" / f"{course.id}.md").write_text(f"ABOUT {course.id}", encoding="utf-8")
    return root


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "professor.json"
    path.write_text(json.dumps({
        "gemini_model": "test-model", "chunk_pages": 6, "render_dpi": 72, "max_history_turns": 10,
        "courses": [{"id": item.id, "name": item.name, "folder": item.folder} for item in COURSES],
    }), encoding="utf-8")
    return path


def pdf_of(path, pages):
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(200, 200)
    with open(path, "wb") as out:
        writer.write(out)
    return path


def lesson_of(tmp_path, gemini, pages=30, chunk=6, waits=None):
    slides = slide_loader.load([pdf_of(tmp_path / "lecture.pdf", pages)])
    return Lesson(COURSES[0], "SYSTEM", "Week 9", "exam prep", slides, model_with(gemini, waits), config(chunk))


# Acceptance test 1: only the core and the one chosen course are loaded.


def test_only_the_core_and_the_chosen_course_are_loaded(prompts, caplog):
    caplog.set_level("INFO")
    prompt = professor.build_system_prompt("manufacturing_process", prompts)
    assert prompt == "CORE RULES\n\nABOUT manufacturing_process"
    assert "ABOUT cad" not in prompt
    assert "core.md, manufacturing_process.md" in caplog.text


def test_a_missing_prompt_file_is_reported(prompts):
    with pytest.raises(ProfessorSetupError, match="nothing.md"):
        professor.build_system_prompt("nothing", prompts)


# Acceptance test 2: long lectures go in chunks, with no slide skipped.


def test_a_thirty_page_lecture_is_taught_in_continuous_chunks(tmp_path):
    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini)
    while not lesson.finished:
        lesson.teach_next()
    asked = [call[1][-1]["parts"][-1]["text"] for call in gemini.calls]
    spans = [tuple(int(n) for n in re.search(r"slides (\d+)–(\d+) using", text).groups()) for text in asked]
    assert spans == [(1, 6), (7, 12), (13, 18), (19, 24), (25, 30)]
    assert all("Total slides: 30" in text for text in asked)


def test_each_request_carries_exactly_its_own_pages(tmp_path):
    from pypdf import PdfReader
    import io

    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini, pages=8)
    lesson.teach_next()
    lesson.teach_next()
    sizes = []
    for call in gemini.calls:
        data = call[1][-1]["parts"][0]["inline_data"]
        assert data["mime_type"] == "application/pdf"
        sizes.append(len(PdfReader(io.BytesIO(data["data"])).pages))
    assert sizes == [6, 2]


def test_the_pointer_stays_put_when_teaching_fails(tmp_path):
    gemini = Gemini([Failure(403)])
    lesson = lesson_of(tmp_path, gemini)
    with pytest.raises(TeachingError):
        lesson.teach_next()
    assert lesson.pointer == 0
    lesson.teach_next()
    assert lesson.pointer == 6


def test_a_pdf_gemini_refuses_is_sent_as_page_pictures(tmp_path):
    gemini = Gemini([Failure(400)])
    lesson = lesson_of(tmp_path, gemini, pages=2)
    lesson.teach_next()
    kinds = [part["inline_data"]["mime_type"] for part in gemini.calls[1][1][-1]["parts"] if "inline_data" in part]
    assert kinds == ["image/png", "image/png"]


def test_pictures_and_pdfs_stay_in_the_order_chosen(tmp_path):
    first = pdf_of(tmp_path / "a.pdf", 2)
    picture = tmp_path / "b.png"
    picture.write_bytes(b"not really a picture")
    last = pdf_of(tmp_path / "c.pdf", 1)
    slides = slide_loader.load([first, picture, last])
    assert len(slides) == 4
    assert [kind for _, kind in slide_loader.as_files(slides)] == ["application/pdf", "image/png", "application/pdf"]


def test_two_pdfs_stay_separate_and_in_order(tmp_path):
    slides = slide_loader.load([pdf_of(tmp_path / "a.pdf", 1), pdf_of(tmp_path / "b.pdf", 1)])
    assert len(slide_loader.as_files(slides)) == 2


@pytest.mark.parametrize("name", ["notes.docx", "missing.png"])
def test_unusable_files_are_refused(tmp_path, name):
    with pytest.raises(SlideError):
        slide_loader.load([tmp_path / name])


def test_a_broken_pdf_is_refused(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    with pytest.raises(SlideError, match="broken.pdf"):
        slide_loader.load([path])


# Acceptance test 3: only the first line is spoken.


def test_only_the_first_line_is_spoken():
    reply = renderer.split("We will start with vacuum.\n### Slide 1\n| a | b |\n$x^2$")
    assert reply.spoken == "We will start with vacuum."
    assert "| a | b |" in reply.screen and "$x^2$" in reply.screen
    assert "start with vacuum" not in reply.screen


@pytest.mark.parametrize("first", ["### Slide 1", "| a | b |", "$E = mc^2$", "- item", "1. item", "x" * 300])
def test_a_first_line_that_is_not_speech_is_never_read_aloud(first):
    reply = renderer.split(f"{first}\nMore text")
    assert reply.spoken == renderer.FALLBACK_SPOKEN
    assert first in reply.screen


def test_a_reply_of_one_line_still_shows_on_screen():
    assert renderer.split("Done for today.").screen == "Done for today."


# Acceptance test 4: a question does not move the pointer.


def test_a_question_keeps_the_place_and_remembers_the_last_slides(tmp_path):
    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini)
    lesson.teach_next()
    lesson.ask("simpler")
    assert lesson.pointer == 6
    contents = gemini.calls[-1][1]
    assert contents[-1]["parts"] == [{"text": "simpler"}]
    assert "Here are the slides" in contents[-2]["parts"][0]["text"]


def test_old_turns_are_trimmed(tmp_path):
    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini)
    for number in range(15):
        lesson.ask(f"question {number}")
    assert len(gemini.calls[-1][1]) == 21


# Acceptance test 6: a rate limit is shown, retried, and never moves to another model.


def test_a_rate_limit_shows_a_message_and_tries_again(tmp_path):
    gemini, waits, slept = Gemini([Failure(429), Failure(429)]), [], []
    assert model_with(gemini, waits, slept).ask("s", [], "hello")
    assert len(gemini.calls) == 3 and all(call[0] == "test-model" for call in gemini.calls)
    assert len(waits) == 2 and "busy" in waits[0]
    assert slept == [4.0, 8.0]


def test_after_three_tries_it_stops_and_says_so(tmp_path):
    gemini = Gemini([Failure(429), Failure(429), Failure(429), Failure(429)])
    with pytest.raises(TeachingError, match="busy"):
        model_with(gemini).ask("s", [], "hello")
    assert len(gemini.calls) == 3


def test_no_key_is_reported_and_nothing_is_sent():
    gemini = Gemini()
    model = ProfessorModel("m", load_key=lambda: "", make_client=lambda key: gemini)
    with pytest.raises(TeachingError, match="key"):
        model.ask("s", [], "hello")
    assert gemini.calls == []


def test_a_refused_file_is_its_own_failure():
    with pytest.raises(SlidesRefused):
        model_with(Gemini([Failure(400)])).ask("s", [], "hello", [(b"x", "application/pdf")])


def test_this_module_cannot_reach_groq_or_grok():
    source = llm_router.__file__
    with open(source, encoding="utf-8") as file:
        code = file.read().lower()
    assert "groq" not in code and "grok" not in code


# Acceptance test 8: editing a course file changes the next class.


def test_editing_a_course_file_changes_the_next_class(prompts):
    (prompts / "courses" / "cad.md").write_text("NEW EXAM FORMAT", encoding="utf-8")
    assert "NEW EXAM FORMAT" in professor.build_system_prompt("cad", prompts)


def test_no_exam_details_live_in_the_code():
    for module in (professor, llm_router, renderer, slide_loader):
        with open(module.__file__, encoding="utf-8") as file:
            code = file.read()
        assert "MCQ" not in code and "TOPIK" not in code and "D:\\" not in code


# Words.


@pytest.mark.parametrize("text", ["professor mode", "Professor Mode.", "start class", "start the class", "teach me", "please teach me"])
def test_entering(text):
    assert professor.is_enter(text)


@pytest.mark.parametrize("text", ["open notepad", "what does a professor do", "my teacher is kind"])
def test_normal_words_do_not_enter(text):
    assert not professor.is_enter(text)


@pytest.mark.parametrize("text", ["exit professor mode", "end class", "End the class."])
def test_leaving(text):
    assert professor.is_exit(text)


@pytest.mark.parametrize("text", ["Alpha, open notepad", "hey alpha what time is it", "aplha play music"])
def test_a_message_to_aplha_during_class_is_recognised(text):
    assert professor.is_for_aplha(text)


@pytest.mark.parametrize("text", ["what is an alpha particle", "alpha particles in ion implantation", "alpha decay"])
def test_a_question_about_alpha_particles_stays_with_the_professor(text):
    assert not professor.is_for_aplha(text)


@pytest.mark.parametrize(
    "said, course_id",
    [("manufacturing", "manufacturing_process"), ("semiconductor equipment", "equipment"),
     ("CAD", "cad"), ("control engineering", "control_engineering"), ("materials and components", "materials_components_equipment")],
)
def test_finding_a_course_from_speech(said, course_id):
    assert professor.find_course(said, COURSES).id == course_id


@pytest.mark.parametrize("said", ["banana", "semiconductor"])
def test_an_unclear_course_is_not_guessed(said):
    assert professor.find_course(said, COURSES) is None


def test_goals():
    assert professor.find_goal("week 9, exam prep") == "exam prep"
    assert professor.find_goal("quick review") == "quick review"
    assert professor.find_goal("week 9") == "learn new material"


def test_the_chunk_size_is_kept_within_limits(config_file):
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["chunk_pages"] = 50
    config_file.write_text(json.dumps(raw), encoding="utf-8")
    assert professor.load_config(config_file).chunk_pages == 8


def test_a_missing_config_is_reported(tmp_path):
    with pytest.raises(ProfessorSetupError, match="nope.json"):
        professor.load_config(tmp_path / "nope.json")


# The class window.


class FakeConversation(QObject):
    notice = Signal(str)

    def __init__(self):
        super().__init__()
        self.spoken, self.class_changes, self.stopped = [], [], 0

    def set_class(self, on):
        self.class_changes.append(on)

    def say_aloud(self, text):
        self.spoken.append(text)

    def interrupt(self):
        self.stopped += 1


@pytest.fixture
def room(qapp, prompts, config_file, tmp_path, monkeypatch):
    gemini = Gemini()
    pdf = pdf_of(tmp_path / "lecture.pdf", 8)
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a: ([str(pdf)], ""))
    conversation = FakeConversation()
    window = ProfessorWindow(
        conversation, config_path=config_file, prompts=prompts,
        make_model=lambda name, on_wait: model_with(gemini, []),
    )
    yield window, conversation, gemini
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def teaching(window):
    window.start_class()
    window.handle("manufacturing")
    window.handle("week 9, exam prep")
    assert spin_until(lambda: not window._busy and window._lesson and window._lesson.pointer == 6)


def test_a_class_asks_for_the_course_then_the_lecture_then_teaches(room):
    window, conversation, gemini = room
    teaching(window)
    assert conversation.class_changes == [True]
    assert conversation.spoken[0] == "Which course shall we study?"
    assert gemini.calls[0][2] == {"system_instruction": "CORE RULES\n\nABOUT manufacturing_process"}
    assert "Goal: exam prep" in gemini.calls[0][1][-1]["parts"][-1]["text"]


def test_only_the_first_line_reaches_the_voice(room):
    window, conversation, _ = room
    teaching(window)
    assert conversation.spoken[-1] == "Here are the slides."
    assert "Here are the slides." not in window.notes.toPlainText()
    assert "a" in window.notes.toPlainText()


def test_next_teaches_the_following_slides_and_shows_progress(room):
    window, _, _ = room
    teaching(window)
    window.handle("next")
    assert spin_until(lambda: not window._busy and window._lesson.pointer == 8)
    assert "8 of 8" in window.status.text()


def test_the_next_button_does_the_same(room):
    window, _, _ = room
    teaching(window)
    window.next_button.click()
    assert spin_until(lambda: window._lesson.pointer == 8)


def test_saying_end_class_leaves_and_restores_normal_speech(room):
    window, conversation, _ = room
    teaching(window)
    window.handle("end class")
    assert conversation.class_changes == [True, False]
    assert not window.in_class and not window.isVisible()


def test_a_failure_is_shown_and_the_class_carries_on(room):
    window, _, gemini = room
    teaching(window)
    gemini.failures = [Failure(403)]
    window.handle("next")
    assert spin_until(lambda: "could not answer" in window.status.text())
    assert window._lesson.pointer == 6 and window.next_button.isEnabled()


def test_no_slides_chosen_keeps_the_class_waiting(room, monkeypatch):
    window, _, gemini = room
    monkeypatch.setattr(QFileDialog, "getOpenFileNames", lambda *a: ([], ""))
    window.start_class()
    window.handle("cad")
    window.handle("week 1")
    assert gemini.calls == [] and window._lesson is None and window.in_class


def test_an_unclear_course_asks_again(room):
    window, conversation, _ = room
    window.start_class()
    window.handle("banana")
    assert "did not catch" in conversation.spoken[-1]


def test_a_message_while_gemini_is_working_does_not_start_a_second_call(room):
    window, _, gemini = room
    teaching(window)
    held, release = [], threading.Event()
    original = window._lesson._model.ask

    def slow(*arguments):
        held.append(1)
        release.wait(5)
        return original(*arguments)

    window._lesson._model.ask = slow
    window.handle("next")
    window.handle("next")
    assert spin_until(lambda: held)
    release.set()
    assert spin_until(lambda: not window._busy)
    assert len(held) == 1


def test_a_course_name_keeps_its_ampersand(room):
    window, _, _ = room
    window.start_class()
    labels = [window._course_grid.itemAt(index).widget().text() for index in range(window._course_grid.count())]
    assert "Intro to Semiconductor Materials, Components && Equipment" in labels


def test_the_first_lesson_opens_at_its_top(room):
    window, _, gemini = room
    window.start_class()
    window.handle("cad")
    window.show()
    window.notes.show()
    window._add_note("\n\n".join(f"line {number}" for number in range(300)))
    assert window.notes.verticalScrollBar().value() == 0


def test_esc_stops_the_voice_and_keeps_the_class(room):
    window, conversation, _ = room
    teaching(window)
    window.reject()
    assert conversation.stopped == 1 and window.in_class
