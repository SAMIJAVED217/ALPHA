"""The Wheelix mentor, with a pretend Gemini and a pretend computer. Nothing here runs a real command."""

import gc
import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal

from alpha.mentor import environment, mentor
from alpha.mentor.mentor import APP_RULES, MentorCourse, MentorSetupError, is_enter, take_progress
from alpha.professor.llm_router import TeachingError
from alpha.tools.catalogue import MENTOR_TOOL, build_toolbox
from alpha.ui.mentor_window import MentorWindow

from .test_interface import spin_until as wait_briefly


def spin_until(condition):
    return wait_briefly(condition, seconds=10)


class Gemini:
    def __init__(self, replies=None):
        self.calls = []
        self.replies = list(replies or [])
        self.model = mentor.MODEL
        self.error = None

    def ask(self, system, history, text, files=()):
        self.calls.append((system, list(history), text))
        if self.error:
            raise self.error
        if self.replies:
            return self.replies.pop(0)
        return f"Here is the next part.\n\nNotes for {text[:20]}\n\nPROGRESS: level 0 | Lesson 1: Robots | teaching"


def course(gemini=None):
    return MentorCourse("OWNER PROMPT", gemini or Gemini())


# The course.


def test_the_owners_prompt_and_the_app_rules_both_reach_gemini():
    gemini = Gemini()
    course(gemini).respond("hello")
    system = gemini.calls[0][0]
    assert system.startswith("OWNER PROMPT") and APP_RULES in system


def test_the_first_session_asks_for_the_assessment_and_carries_the_measurements():
    gemini = Gemini()
    made = course(gemini)
    made.begin("- Operating system: Windows 11 Pro")
    sent = gemini.calls[0][2]
    assert "section 13" in sent and "Windows 11 Pro" in sent
    assert made.transcript and not made.transcript[0].startswith("**You:**")


def test_the_progress_line_is_kept_and_never_shown():
    reply = course().respond("hello")
    assert "PROGRESS" not in reply.screen


def test_progress_is_read_from_the_reply():
    made = course(Gemini(["Good.\n\nWork.\n\nPROGRESS: level 2 | Lesson 4: Rotation matrices | checking"]))
    made.respond("hi")
    assert made.progress == {"level": 2, "lesson": "Lesson 4: Rotation matrices", "state": "checking"}


def test_a_passed_lesson_is_remembered_once_and_told_to_gemini():
    replies = ["Well done.\n\nPROGRESS: level 0 | Lesson 1: Robots | passed"] * 2
    gemini = Gemini(replies + [None])
    gemini.replies[-1] = "Next.\n\nPROGRESS: level 1 | Lesson 2: Nodes | teaching"
    made = course(gemini)
    made.respond("a")
    made.respond("b")
    made.respond("c")
    assert made.passed == ["Lesson 1: Robots"]
    assert "Lesson 1: Robots" in gemini.calls[-1][0]


@pytest.mark.parametrize(
    "line",
    ["PROGRESS: level 13 | x | teaching", "PROGRESS: level 1 | x | finished", "progress level 1 x teaching"],
)
def test_a_nonsense_progress_line_changes_nothing(line):
    _, found = take_progress(f"Text\n{line}")
    assert found is None


def test_a_bold_progress_line_is_still_read():
    text, found = take_progress("Text\n**PROGRESS: level 3 | Lesson 7: Encoders | teaching**")
    assert found["level"] == 3 and text == "Text"


def test_a_reply_without_progress_keeps_the_old_progress():
    made = course(Gemini(["Just words."]))
    made.respond("hi")
    assert made.progress["state"] == "assessing"


def test_old_turns_fold_into_a_summary():
    gemini = Gemini()
    made = course(gemini)
    for number in range(mentor.MAX_HISTORY_TURNS + 1):
        made.respond(f"turn {number}")
    assert made.summary and len(made.history) <= 2 * mentor.MAX_HISTORY_TURNS
    made.respond("after the fold")
    assert f"Earlier in this course:\n{made.summary}" in gemini.calls[-1][0]


def test_a_course_survives_saving_and_loading(tmp_path):
    made = course(Gemini(["A.\n\nPROGRESS: level 0 | Lesson 1: Robots | passed"]))
    made.respond("hello")
    mentor.save(made, tmp_path / "course.json")
    back = MentorCourse.restored(mentor.load_saved(tmp_path / "course.json"), "NEW PROMPT", Gemini())
    assert back.history == made.history and back.passed == ["Lesson 1: Robots"]
    assert back.progress == made.progress and back._prompt == "NEW PROMPT"


def test_no_saved_course_is_not_an_error(tmp_path):
    assert mentor.load_saved(tmp_path / "missing.json") is None


def test_a_damaged_saved_course_is_reported(tmp_path):
    path = tmp_path / "course.json"
    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(MentorSetupError):
        mentor.load_saved(path)


def test_a_missing_prompt_is_reported(tmp_path):
    with pytest.raises(MentorSetupError, match="missing"):
        mentor.load_prompt(tmp_path / "nothing.md")


def test_the_resources_are_named_and_must_be_checked_before_installing():
    for name in ("slam_toolbox", "navigation2", "robot_localization", "PythonRobotics", "REP 2000"):
        assert name in APP_RULES
    assert "check the current version" in APP_RULES and "Never invent" in APP_RULES


@pytest.mark.parametrize("said", ["robotics class", "Start the robotics class.", "wheelix mentor", "open SLAM course",
                                  "please start my wheelix lesson"])
def test_entering_the_mentor_by_words(said):
    assert is_enter(said)


@pytest.mark.parametrize("said", ["professor mode", "start class", "tell me about robotics", "slam the door"])
def test_other_words_do_not_open_the_mentor(said):
    assert not is_enter(said)


def test_the_tool_is_offered_and_runs_at_once():
    tool = build_toolbox(None).get(MENTOR_TOOL)
    assert tool.run({})["ok"] and not tool.needs_confirmation


# Measuring the computer.


def fake_run(answers):
    def run(command):
        for key, answer in answers.items():
            if key in " ".join(command):
                return answer
        return ""

    return run


def test_the_measurement_covers_windows_hardware_wsl_and_ros():
    report = environment.measure(fake_run({
        "Win32_VideoController": "AMD Radeon(TM) Vega 8 Graphics",
        "--list": "  NAME            STATE           VERSION\n* Ubuntu-24.04    Stopped         2",
        "--status": "Default Version: 2",
        "os-release": 'PRETTY_NAME="Ubuntu 24.04.1 LTS"\nROS: jazzy\nGAZEBO: /usr/bin/gz',
    }))
    for part in ("Windows", "Memory", "Vega 8", "Ubuntu-24.04", "default version: 2", "ROS: jazzy", "/usr/bin/gz"):
        assert part in report


def test_without_wsl_it_says_so_and_does_not_look_inside():
    seen = []

    def run(command):
        seen.append(command)
        return ""

    assert "WSL: not installed" in environment.measure(run)
    assert environment.INSIDE_LINUX not in seen


def test_only_fixed_read_only_commands_are_used():
    for command in (environment.GPU_COMMAND, environment.WSL_LIST, environment.WSL_STATUS, environment.INSIDE_LINUX):
        words = " ".join(command)
        for risky in ("install", "rm ", "apt", "sudo", "Set-", "Remove-"):
            assert risky not in words
        # The only redirection allowed is throwing away error messages.
        assert ">" not in words.replace("2>/dev/null", "")


@pytest.mark.parametrize(
    "raw, text",
    [("Ubuntu".encode("utf-16-le"), "Ubuntu"), ("﻿Ubuntu".encode("utf-16-le"), "Ubuntu"), (b"jazzy", "jazzy")],
)
def test_wsl_output_is_decoded_either_way(raw, text):
    assert environment._text_of(raw) == text


# The window.


class FakeConversation(QObject):
    notice = Signal(str)

    def __init__(self):
        super().__init__()
        self.spoken, self.class_changes, self.interrupted = [], [], 0

    def set_class(self, on):
        self.class_changes.append(on)

    def say_aloud(self, text):
        self.spoken.append(text)

    def interrupt(self):
        self.interrupted += 1


@pytest.fixture
def desk(qapp, tmp_path):
    prompt = tmp_path / "wheelix.md"
    prompt.write_text("OWNER PROMPT", encoding="utf-8")
    gemini, conversation = Gemini(), FakeConversation()
    window = MentorWindow(conversation, prompt_path=prompt, saved_path=tmp_path / "saved.json",
                          make_model=lambda name, on_wait, spare: gemini, measure=lambda: "- Memory: 9.7 GB")
    yield window, conversation, gemini, tmp_path
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    gc.collect()


def test_a_first_start_measures_the_computer_and_begins_the_assessment(desk):
    window, conversation, gemini, folder = desk
    window.start_class()
    assert spin_until(lambda: gemini.calls and not window._busy)
    assert "9.7 GB" in gemini.calls[0][2]
    assert conversation.class_changes == [True] and conversation.spoken == ["Here is the next part."]
    assert (folder / "saved.json").exists()
    assert "Level 0" in window.progress.text()


def test_answers_are_sent_and_the_course_saved(desk):
    window, _, gemini, folder = desk
    window.start_class()
    assert spin_until(lambda: not window._busy and gemini.calls)
    window.handle("A robot that moves by itself")
    assert spin_until(lambda: len(gemini.calls) == 2 and not window._busy)
    assert gemini.calls[1][2] == "A robot that moves by itself"
    assert "A robot that moves by itself" in json.loads((folder / "saved.json").read_text(encoding="utf-8"))["transcript"][1]


def test_check_my_pc_sends_fresh_measurements(desk):
    window, _, gemini, _ = desk
    window.start_class()
    assert spin_until(lambda: not window._busy and gemini.calls)
    window.handle("Check my PC")
    assert spin_until(lambda: len(gemini.calls) == 2 and not window._busy)
    assert "measured on my computer" in gemini.calls[1][2]


def test_a_saved_course_resumes_where_it_stopped_without_asking_gemini(desk):
    window, conversation, gemini, folder = desk
    window.start_class()
    assert spin_until(lambda: not window._busy and gemini.calls)
    window.end_class()
    gemini.calls.clear()
    window.start_class()
    assert spin_until(lambda: not window._busy and conversation.spoken[-1].startswith("Welcome back"))
    assert gemini.calls == []


def test_end_class_hands_the_microphone_back(desk):
    window, conversation, _, _ = desk
    window.start_class()
    assert spin_until(lambda: not window._busy)
    window.handle("end class")
    assert not window.in_class and conversation.class_changes == [True, False]


def test_a_failure_is_shown_and_the_course_carries_on(desk):
    window, _, gemini, _ = desk
    gemini.error = TeachingError("Gemini is still busy.")
    window.start_class()
    assert spin_until(lambda: "busy" in window.status.text())
    assert window.in_class and window.send_button.isEnabled()


def test_a_missing_prompt_is_told_and_nothing_opens(qapp, tmp_path):
    conversation = FakeConversation()
    notices = []
    conversation.notice.connect(notices.append)
    window = MentorWindow(conversation, prompt_path=tmp_path / "none.md", saved_path=tmp_path / "s.json",
                          make_model=lambda name, on_wait, spare: Gemini(), measure=lambda: "")
    try:
        window.start_class()
        assert notices and "missing" in notices[0] and not window.in_class
    finally:
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()


def test_esc_stops_the_voice_and_keeps_the_class(desk):
    window, conversation, _, _ = desk
    window.start_class()
    assert spin_until(lambda: not window._busy)
    window.reject()
    assert conversation.interrupted == 1 and window.in_class
