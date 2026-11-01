"""Professor Mode, with a pretend Gemini. The real prompt and config files are never read here."""

import gc
import json
import re
import threading
from datetime import date

import pytest
from pypdf import PdfWriter
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtWidgets import QFileDialog

from alpha.professor import llm_router, math_render, renderer, session_store, slide_loader
from alpha.professor import professor_mode as professor
from alpha.professor.llm_router import ProfessorModel, SlidesRefused, TeachingError
from alpha.professor.professor_mode import Course, Lesson, ProfessorConfig, ProfessorSetupError
from alpha.professor.slide_loader import Slide, SlideError
from alpha.tools.registry import ToolRefused
from alpha.ui.professor_window import ProfessorWindow

from .test_interface import spin_until as wait_briefly


def spin_until(condition):
    """The first equation loads matplotlib, which takes seconds on a busy computer."""
    return wait_briefly(condition, seconds=25)


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
    assert len(lesson._history) <= 20 and lesson.summary


# Acceptance test 6: a rate limit is shown, retried, and never moves to another model.


def test_a_rate_limit_shows_a_message_and_tries_again(tmp_path):
    gemini, waits, slept = Gemini([Failure(429), Failure(429)]), [], []
    assert model_with(gemini, waits, slept).ask("s", [], "hello")
    assert len(gemini.calls) == 3 and all(call[0] == "test-model" for call in gemini.calls)
    assert waits[0] == "Gemini is busy. Trying again in 4 seconds (try 2 of 3)."
    assert sum(slept) == 12


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


# Acceptance test 5: a quiz asks, waits, grades and scores.


class Marker(Gemini):
    """Grades every answer with the next result in the list."""

    def __init__(self, grades):
        super().__init__()
        self.grades = list(grades)

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents, config))
        said = contents[-1]["parts"][-1]["text"]
        grade = f"\nRESULT: {self.grades.pop(0)}" if said.startswith("My answer") else ""
        return type("Reply", (), {"text": f"Next question.\nWhich gas is used?\n`next | simpler`{grade}"})()


def test_a_quiz_counts_right_and_wrong_answers(tmp_path):
    gemini = Marker(["correct", "wrong", "correct"])
    lesson = lesson_of(tmp_path, gemini)
    lesson.respond("quiz me")
    assert lesson.quizzing and lesson.graded == 0
    for answer in ("b", "c", "argon"):
        lesson.respond(answer)
    assert (lesson.correct, lesson.graded) == (2, 3)


def test_the_grade_line_never_reaches_the_screen_or_voice(tmp_path):
    lesson = lesson_of(tmp_path, Marker(["correct"]))
    lesson.respond("quiz me")
    reply = lesson.respond("b")
    assert "RESULT" not in reply.screen and "RESULT" not in reply.spoken


def test_the_grade_line_is_not_kept_in_the_conversation(tmp_path):
    gemini = Marker(["correct", "correct"])
    lesson = lesson_of(tmp_path, gemini)
    lesson.respond("quiz me")
    lesson.respond("b")
    lesson.respond("c")
    assert "RESULT:" not in gemini.calls[-1][1][-2]["parts"][0]["text"]


def test_a_reply_with_no_grade_is_not_counted(tmp_path):
    lesson = lesson_of(tmp_path, Gemini())
    lesson.respond("quiz me")
    lesson.respond("b")
    assert lesson.graded == 0


@pytest.mark.parametrize("line", ["RESULT: correct", "**RESULT: correct**", "result: Correct.", "`RESULT: correct`"])
def test_grades_are_read_in_any_style(tmp_path, line):
    class Styled(Gemini):
        def generate_content(self, model, contents, config):
            return type("Reply", (), {"text": f"Good.\nMore.\n{line}"})()

    lesson = lesson_of(tmp_path, Styled())
    lesson.respond("quiz me")
    lesson.respond("b")
    assert (lesson.correct, lesson.graded) == (1, 1)


def test_a_control_during_a_quiz_is_not_treated_as_an_answer(tmp_path):
    lesson = lesson_of(tmp_path, Marker([]))
    lesson.respond("quiz me")
    lesson.respond("Why?")
    assert lesson.graded == 0 and lesson.quizzing


def test_next_leaves_the_quiz_and_teaches(tmp_path):
    lesson = lesson_of(tmp_path, Gemini())
    lesson.respond("quiz me")
    lesson.respond("next")
    assert not lesson.quizzing and lesson.pointer == 6


def test_stopping_a_quiz_reports_the_score_without_a_call(tmp_path):
    gemini = Marker(["correct"])
    lesson = lesson_of(tmp_path, gemini)
    lesson.respond("quiz me")
    lesson.respond("b")
    before = len(gemini.calls)
    assert "1 out of 1" in lesson.respond("stop quiz").spoken
    assert len(gemini.calls) == before and not lesson.quizzing


@pytest.mark.parametrize("word", ["simpler", "Deeper", "example", "Why?", "summary"])
def test_control_words_reach_gemini_and_keep_the_place(tmp_path, word):
    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini)
    lesson.teach_next()
    lesson.respond(word)
    assert lesson.pointer == 6
    assert gemini.calls[-1][1][-1]["parts"] == [{"text": word}]


# The end of a lecture.


def test_after_the_last_slide_the_lecture_closes_once(tmp_path):
    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini, pages=6)
    lesson.teach_next()
    assert lesson.needs_closing
    lesson.teach_next()
    assert "end-of-lecture" in gemini.calls[-1][1][-1]["parts"][-1]["text"]
    assert not lesson.needs_closing
    calls = len(gemini.calls)
    assert "last slide" in lesson.teach_next().spoken
    assert len(gemini.calls) == calls


def test_a_failed_closing_can_be_tried_again(tmp_path):
    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini, pages=6)
    lesson.teach_next()
    gemini.failures = [Failure(403)]
    with pytest.raises(TeachingError):
        lesson.teach_next()
    assert lesson.needs_closing
    lesson.teach_next()
    assert lesson.closed


def test_the_window_closes_the_lecture_by_itself(room):
    window, conversation, gemini = room
    teaching(window)
    window.handle("next")
    assert spin_until(lambda: window._lesson.closed and not window._busy)
    assert "end-of-lecture" in gemini.calls[-1][1][-1]["parts"][-1]["text"]
    assert len(gemini.calls) == 3


# Equations.


def test_equations_become_pictures():
    prepared = math_render.prepare(r"Using $\frac{dN}{dt}$ and" + "\n" + r"$$E = mc^2$$")
    assert "$" not in prepared.markdown
    assert len(prepared.pictures) == 2
    assert all(data.startswith(b"\x89PNG") for data in prepared.pictures.values())
    assert all(f"({name})" in prepared.markdown for name in prepared.pictures)


@pytest.mark.parametrize(
    "latex, words",
    [(r"800\text{–}900\ ^\circ\text{C}", "800–900 °C"), (r"1{,}000\ ^\circ\text{C}", "1,000 °C"),
     (r"\ge 450\ ^\circ\text{C}", "≥ 450 °C"), (r"4\text{ to }5\text{ hours}", "4 to 5 hours")],
)
def test_simple_values_become_normal_text(latex, words):
    prepared = math_render.prepare(f"At ${latex}$ here")
    assert prepared.pictures == {} and prepared.markdown == f"At {words} here"


def test_one_equation_drawn_twice_makes_one_picture():
    prepared = math_render.prepare("$x^2$ and again $x^2$")
    assert len(prepared.pictures) == 1


def test_code_blocks_are_left_alone():
    source = "```\nprice = $5 and $6\n```\nand `$x$`"
    assert math_render.prepare(source).markdown == source


@pytest.mark.parametrize("text", ["It costs $5 and $6 today", "Pay $20.", "A$ and B$"])
def test_money_is_not_mistaken_for_an_equation(text):
    assert math_render.prepare(text).pictures == {}


@pytest.mark.parametrize(
    "latex, words",
    [(r"\begin{bmatrix}1 & 2\\3 & 4\end{bmatrix}", "1 2; 3 4"), (r"\text{식각}", "식각")],
)
def test_what_cannot_be_drawn_becomes_plain_text(latex, words):
    prepared = math_render.prepare(f"See ${latex}$ here")
    assert prepared.pictures == {} and words in prepared.markdown and "\\" not in prepared.markdown


def test_plain_text_is_readable():
    assert math_render.plain(r"800\ ^\circ\text{C}") == "800 °C"


def test_the_window_shows_equation_pictures(room):
    window, _, gemini = room
    teaching(window)
    assert window.notes.pictures
    name = next(iter(window.notes.pictures))
    assert "$E" not in window.notes.toPlainText()
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument

    assert not window.notes.loadResource(QTextDocument.ResourceType.ImageResource, QUrl(name)).isNull()


def test_every_control_button_sends_its_word(room):
    window, _, gemini = room
    teaching(window)
    for word in ("Simpler", "Deeper", "Example", "Why?", "Summary"):
        window.controls[word].click()
        assert spin_until(lambda: not window._busy)
        assert gemini.calls[-1][1][-1]["parts"] == [{"text": word}]


def test_the_score_shows_during_a_quiz(room, monkeypatch):
    window, _, gemini = room
    teaching(window)
    assert window.score.isHidden()
    window.controls["Quiz me"].click()
    assert spin_until(lambda: not window._busy)
    assert not window.score.isHidden() and window.score.text() == "Quiz score: 0 / 0"


# Saving, resuming and exporting.


def test_a_class_comes_back_exactly_where_it_was(tmp_path):
    gemini = Marker(["correct"])
    lesson = lesson_of(tmp_path, gemini)
    lesson.teach_next()
    lesson.respond("quiz me")
    lesson.respond("b")
    folder = tmp_path / "sessions"
    session_store.save(lesson, folder)
    saved = session_store.saved_classes(folder)[0]
    assert saved.label == "Week 9 (slide 6 of 30)"
    slides = slide_loader.load(saved.data["files"])
    back = Lesson.restored(saved.data, COURSES[0], "NEW SYSTEM", slides, model_with(gemini), config())
    assert (back.pointer, back.correct, back.graded, back.quizzing) == (6, 1, 1, True)
    assert back.transcript == lesson.transcript
    back.respond("next")
    assert "slides 7–12" in gemini.calls[-1][1][-1]["parts"][-1]["text"]
    assert gemini.calls[-1][2]["system_instruction"] == "NEW SYSTEM"


def test_changed_slides_are_refused(tmp_path):
    lesson = lesson_of(tmp_path, Gemini(), pages=8)
    data = lesson.to_saved()
    with pytest.raises(ProfessorSetupError, match="changed"):
        Lesson.restored(data, COURSES[0], "s", [], model_with(Gemini()), config())


def test_no_key_is_ever_saved(tmp_path):
    lesson = lesson_of(tmp_path, Gemini())
    lesson.teach_next()
    assert "a-key" not in session_store.save(lesson, tmp_path / "s").read_text(encoding="utf-8")


def test_a_damaged_save_is_skipped(tmp_path):
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "other.json").write_text("{}", encoding="utf-8")
    assert session_store.saved_classes(tmp_path) == []


def test_no_sessions_folder_means_no_saved_classes(tmp_path):
    assert session_store.saved_classes(tmp_path / "nowhere") == []


def test_saving_again_replaces_the_same_class(tmp_path):
    lesson = lesson_of(tmp_path, Gemini())
    folder = tmp_path / "s"
    session_store.save(lesson, folder)
    lesson.teach_next()
    session_store.save(lesson, folder)
    assert len(list(folder.glob("*"))) == 1


def exportable(tmp_path):
    folder = tmp_path / "course"
    folder.mkdir()
    slides = slide_loader.load([pdf_of(tmp_path / "lecture.pdf", 6)])
    course = Course("cad", "Materials, Parts & Tools", str(folder))
    lesson = Lesson(course, "s", "Week 9: etching?", "exam prep", slides, model_with(Gemini()), config())
    lesson.teach_next()
    return lesson, folder


def test_export_makes_a_markdown_file_named_as_asked(tmp_path):
    lesson, folder = exportable(tmp_path)
    path = session_store.export_notes(lesson, date(2026, 11, 2))
    assert path.parent == folder
    assert path.name == "[ALPHA Notes] Materials, Parts & Tools - Week 9 etching - 2026-11-02.md"
    written = path.read_text(encoding="utf-8")
    assert "### Slide" in written and "$E = mc^2$" in written and written.startswith("# Materials")


def test_export_never_replaces_an_earlier_file(tmp_path):
    lesson, _ = exportable(tmp_path)
    first = session_store.export_notes(lesson, date(2026, 11, 2))
    second = session_store.export_notes(lesson, date(2026, 11, 2))
    assert first != second and second.name.endswith(" (2).md") and first.exists()


def test_export_with_no_folder_is_refused(tmp_path):
    lesson, folder = exportable(tmp_path)
    lesson.course = Course("cad", "x", str(tmp_path / "missing"))
    with pytest.raises(ProfessorSetupError, match="folder"):
        session_store.export_notes(lesson)


def test_nothing_is_exported_without_being_asked(room, tmp_path):
    window, _, _ = room
    teaching(window)
    assert not list(tmp_path.glob("**/[[]ALPHA Notes*"))


def test_a_class_saves_itself_and_can_be_resumed_by_voice(room, tmp_path):
    window, conversation, gemini = room
    teaching(window)
    window.end_class()
    window.start_class()
    assert window._saved
    window.handle("resume class")
    assert window._lesson is not None and window._lesson.pointer == 6
    assert spin_until(lambda: "Welcome back" in conversation.spoken[-1])
    assert "6 of 8" in window.status.text()


def test_resuming_with_nothing_saved_says_so(room):
    window, conversation, _ = room
    window.start_class()
    window.handle("resume class")
    assert "no saved class" in conversation.spoken[-1]


def test_a_moved_slide_file_is_reported_on_resume(room, tmp_path):
    window, conversation, _ = room
    teaching(window)
    window.end_class()
    (tmp_path / "lecture.pdf").unlink()
    window.start_class()
    window.handle("resume")
    assert "could not be resumed" in conversation.spoken[-1]


def test_the_export_button_reports_where_the_notes_went(room, tmp_path):
    window, _, _ = room
    teaching(window)
    window._lesson.course = Course("cad", "CAD", str(tmp_path))
    window.controls["Export"].click()
    assert "Notes saved as [ALPHA Notes] CAD" in window.status.text()


def test_resume_and_export_words():
    assert professor.is_enter("resume class") and professor.is_resume("Resume class.")
    assert professor.is_export("export notes") and not professor.is_export("export the data")


# Running summary.


def test_older_turns_become_a_summary_that_reaches_gemini(tmp_path):
    gemini = Gemini()
    lesson = lesson_of(tmp_path, gemini)
    for number in range(12):
        lesson.ask(f"question {number}")
    assert any("Summarise" in str(call[2]) for call in gemini.calls)
    assert "Earlier in this class" in gemini.calls[-1][2]["system_instruction"]


def test_a_failed_summary_does_not_stop_the_class(tmp_path):
    class NoSummary(Gemini):
        def generate_content(self, model, contents, config):
            if "Summarise" in config["system_instruction"]:
                raise Failure(403)
            return super().generate_content(model, contents, config)

    lesson = lesson_of(tmp_path, NoSummary())
    for number in range(12):
        assert lesson.ask(f"question {number}").screen
    assert lesson.summary == "" and len(lesson._history) <= 20


# Groq, which is off unless switched on and never teaches slides.


class Groq:
    def __init__(self, ready=True, error=None):
        self.ready, self.error = ready, error
        self.asked = []

    def connected(self):
        return self.ready

    def ask(self, question, instruction=""):
        self.asked.append((question, instruction))
        if self.error:
            raise self.error
        return "From Groq.\nThe details.\nRESULT: correct"


def groq_lesson(tmp_path, groq, gemini=None):
    slides = slide_loader.load([pdf_of(tmp_path / "lecture.pdf", 12)])
    gemini = gemini or Gemini()
    return Lesson(COURSES[0], "SYSTEM", "Week 9", "exam", slides, model_with(gemini), config(), groq), gemini


def test_groq_is_off_unless_the_config_turns_it_on(config_file):
    assert professor.groq_followups(professor.load_config(config_file)) is None
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw.update(allow_groq_followups=True, groq_model="some-model")
    config_file.write_text(json.dumps(raw), encoding="utf-8")
    assert professor.groq_followups(professor.load_config(config_file)).name == "Groq"


def test_only_a_true_flag_turns_it_on(config_file):
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["allow_groq_followups"] = "yes"
    config_file.write_text(json.dumps(raw), encoding="utf-8")
    assert professor.load_config(config_file).allow_groq_followups is False


@pytest.mark.parametrize("word", ["simpler", "deeper", "Why?"])
def test_groq_answers_allowed_follow_ups_and_is_named(tmp_path, word):
    groq = Groq()
    lesson, gemini = groq_lesson(tmp_path, groq)
    calls = len(gemini.calls)
    reply = lesson.respond(word)
    assert groq.asked and len(gemini.calls) == calls
    assert "Answered by Groq" in reply.screen


def test_groq_can_grade_a_typed_answer(tmp_path):
    groq = Groq()
    lesson, _ = groq_lesson(tmp_path, groq)
    lesson.quizzing = True
    lesson.respond("b")
    assert (lesson.correct, lesson.graded) == (1, 1)


def test_groq_never_teaches_slides_or_closes_a_lecture(tmp_path):
    groq = Groq()
    lesson, gemini = groq_lesson(tmp_path, groq)
    while not lesson.closed:
        lesson.teach_next()
    assert groq.asked == []


@pytest.mark.parametrize("words", ["example", "summary", "what does this mean", "quiz me"])
def test_other_requests_stay_with_gemini(tmp_path, words):
    groq = Groq()
    lesson, _ = groq_lesson(tmp_path, groq)
    lesson.respond(words)
    assert groq.asked == []


def test_a_failing_groq_falls_back_to_gemini_for_a_follow_up(tmp_path):
    lesson, gemini = groq_lesson(tmp_path, Groq(error=ToolRefused("Groq says the usage limit")))
    assert "Groq" not in lesson.respond("simpler").screen
    assert gemini.calls


def test_with_no_groq_key_gemini_answers(tmp_path):
    groq = Groq(ready=False)
    lesson, gemini = groq_lesson(tmp_path, groq)
    lesson.respond("simpler")
    assert groq.asked == [] and gemini.calls


# Groq as the backup when Gemini cannot answer.


def standby_lesson(tmp_path, standby, gemini):
    slides = slide_loader.load([pdf_of(tmp_path / "lecture.pdf", 12)])
    return Lesson(COURSES[0], "SYSTEM", "Week 9", "exam", slides, model_with(gemini), config(), None, standby)


def test_groq_answers_a_question_when_gemini_cannot_and_says_so(tmp_path):
    standby = Groq()
    lesson = standby_lesson(tmp_path, standby, Gemini([Failure(403)]))
    reply = lesson.respond("what is annealing")
    assert standby.asked and "Gemini was unavailable" in reply.screen and "without seeing the slides" in reply.screen


def test_groq_never_stands_in_for_slides_and_the_place_is_kept(tmp_path):
    standby = Groq()
    lesson = standby_lesson(tmp_path, standby, Gemini([Failure(403)]))
    with pytest.raises(TeachingError):
        lesson.teach_next()
    assert standby.asked == [] and lesson.pointer == 0


def test_groq_can_mark_a_quiz_answer_when_gemini_cannot(tmp_path):
    lesson = standby_lesson(tmp_path, Groq(), Gemini([Failure(403)]))
    lesson.quizzing = True
    lesson.respond("b")
    assert (lesson.correct, lesson.graded) == (1, 1)


def test_with_no_groq_key_the_gemini_problem_is_shown(tmp_path):
    lesson = standby_lesson(tmp_path, Groq(ready=False), Gemini([Failure(403)]))
    with pytest.raises(TeachingError, match="could not answer"):
        lesson.respond("what is annealing")


def test_when_groq_fails_too_the_gemini_problem_is_shown(tmp_path):
    lesson = standby_lesson(tmp_path, Groq(error=ToolRefused("no")), Gemini([Failure(403)]))
    with pytest.raises(TeachingError, match="could not answer"):
        lesson.respond("what is annealing")


def test_the_backup_can_be_switched_off(config_file):
    assert professor.groq_standby(professor.load_config(config_file)) is not None
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["groq_when_gemini_fails"] = False
    config_file.write_text(json.dumps(raw), encoding="utf-8")
    assert professor.groq_standby(professor.load_config(config_file)) is None


# Rate limits.


class UsedUp(Failure):
    def __init__(self):
        super().__init__(429)

    def __str__(self):
        return "429 quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier retryDelay: '59698s'"


def spare_model(gemini, waits=None):
    return ProfessorModel("first", load_key=lambda: "k", make_client=lambda key: gemini, sleep=lambda _s: None,
                          on_wait=(waits if waits is not None else []).append, spare=("second", "third"))


def test_a_used_up_model_moves_to_the_next_gemini_model_and_says_so():
    gemini, waits = Gemini([UsedUp()]), []
    model = spare_model(gemini, waits)
    assert model.ask("s", [], "hello")
    assert [call[0] for call in gemini.calls] == ["first", "second"]
    assert model.model == "second" and "switching to second" in waits[0]


def test_the_switch_lasts_so_it_does_not_waste_a_call_each_time():
    gemini = Gemini([UsedUp()])
    model = spare_model(gemini)
    model.ask("s", [], "one")
    model.ask("s", [], "two")
    assert [call[0] for call in gemini.calls] == ["first", "second", "second"]


def test_slides_can_move_to_the_next_gemini_model(tmp_path):
    gemini = Gemini([UsedUp()])
    slides = slide_loader.load([pdf_of(tmp_path / "lecture.pdf", 6)])
    lesson = Lesson(COURSES[0], "s", "Week 9", "exam", slides, spare_model(gemini), config())
    lesson.teach_next()
    assert lesson.pointer == 6 and lesson.model_name == "second"
    assert "inline_data" in gemini.calls[-1][1][-1]["parts"][0]


def test_with_every_model_used_up_it_says_when_and_keeps_the_place():
    gemini = Gemini([UsedUp(), UsedUp(), UsedUp()])
    with pytest.raises(TeachingError, match="about 17 more hours"):
        spare_model(gemini).ask("s", [], "hello")
    assert len(gemini.calls) == 3


def test_a_short_limit_that_mentions_days_is_only_waited_for():
    class Short(Failure):
        def __str__(self):
            return "429 quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier retryDelay: '30s'"

    gemini = Gemini([Short(429)])
    model = spare_model(gemini)
    model.ask("s", [], "hello")
    assert [call[0] for call in gemini.calls] == ["first", "first"]


def test_a_used_up_day_is_not_waited_out():
    slept = []
    gemini = Gemini([UsedUp()])
    with pytest.raises(TeachingError, match="used up"):
        model_with(gemini, slept=slept).ask("s", [], "hello")
    assert slept == []


def test_backup_models_come_from_the_config(config_file):
    raw = json.loads(config_file.read_text(encoding="utf-8"))
    raw["gemini_backup_models"] = ["second"]
    config_file.write_text(json.dumps(raw), encoding="utf-8")
    assert professor.load_config(config_file).gemini_backup_models == ("second",)


def test_the_wait_gemini_asks_for_is_respected():
    class Asked(Failure):
        def __str__(self):
            return "429 Please retry in 23.5s."

    slept = []
    model_with(Gemini([Asked(429)]), slept=slept).ask("s", [], "hello")
    assert sum(slept) == 24


def test_a_very_long_wait_is_capped():
    class Long(Failure):
        def __str__(self):
            return "429 retryDelay: 300s"

    slept = []
    model_with(Gemini([Long(429)]), slept=slept).ask("s", [], "hello")
    assert sum(slept) == 60


def test_the_message_counts_down(tmp_path):
    waits = []
    model_with(Gemini([Failure(429)]), waits).ask("s", [], "hello")
    assert [message.split(" in ")[1].split()[0] for message in waits] == ["4", "3", "2", "1"]
    assert "1 second (" in waits[-1]


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
        conversation, config_path=config_file, prompts=prompts, sessions=tmp_path / "sessions",
        make_model=lambda name, on_wait, spare: model_with(gemini, []),
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
    assert spin_until(lambda: window._lesson.pointer == 8)
    assert spin_until(lambda: "8 of 8" in window.status.text() or "summary" in window.status.text())


def test_the_next_button_does_the_same(room):
    window, _, _ = room
    teaching(window)
    window.controls["Next"].click()
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
    assert window._lesson.pointer == 6 and window.controls["Next"].isEnabled()


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
    window.handle("simpler")
    window.handle("simpler")
    assert spin_until(lambda: held)
    release.set()
    assert spin_until(lambda: not window._busy)
    assert len(held) == 1


@pytest.mark.parametrize("opening", ["---", "---\n\n---", "***", "  ---  "])
def test_the_first_slide_survives_an_opening_line(room, opening):
    window, _, _ = room
    source = f"{opening}\n\n### Slide 1 — First\nWords.\n\n---\n\n### Slide 2 — Second\nMore."
    window.notes.show_notes(source)
    shown = window.notes.toPlainText()
    assert "Slide 1" in shown and "Slide 2" in shown


def test_a_new_lesson_keeps_its_first_slide_on_screen(room):
    window, _, _ = room
    window.start_class()
    window.handle("cad")
    window._add_note(math_render.Prepared("---\n\n### Slide 1 — First\nWords.\n\n---\n\n### Slide 2 — Second"))
    assert "Slide 1" in window.notes.toPlainText()


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
    window._add_note(math_render.Prepared("\n\n".join(f"line {number}" for number in range(300))))
    assert window.notes.verticalScrollBar().value() == 0


def test_esc_stops_the_voice_and_keeps_the_class(room):
    window, conversation, _ = room
    teaching(window)
    window.reject()
    assert conversation.stopped == 1 and window.in_class
