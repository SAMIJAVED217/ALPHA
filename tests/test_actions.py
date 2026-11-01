import pytest

from alpha.actions import (
    EXPIRY_SECONDS,
    ActionGate,
    HumanPress,
    Outcome,
    ProposalRefused,
    validate_file_name,
)


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def gate(clock, tmp_path):
    return ActionGate(clock=clock, folder=tmp_path)


def test_nothing_is_created_by_proposing(gate, tmp_path):
    gate.propose_create_file("note.txt", "hello")
    assert list(tmp_path.iterdir()) == []


def test_confirm_creates_the_file_and_reports_what_was_observed(gate, tmp_path):
    proposal = gate.propose_create_file("note.txt", "hello")
    done = gate.confirm(HumanPress(proposal.id))
    assert done.outcome is Outcome.CREATED
    assert done.as_result()["created"] is True
    assert (tmp_path / "note.txt").read_text() == "hello"
    assert gate.current is None


def test_cancel_creates_nothing(gate, tmp_path):
    proposal = gate.propose_create_file("note.txt", "hello")
    done = gate.cancel(proposal.id)
    assert done.outcome is Outcome.CANCELLED
    assert done.as_result()["created"] is False
    assert list(tmp_path.iterdir()) == []


def test_expiry_creates_nothing_and_a_late_press_does_nothing(gate, clock, tmp_path):
    proposal = gate.propose_create_file("note.txt", "hello")
    clock.now += EXPIRY_SECONDS + 0.1
    with pytest.raises(LookupError):
        gate.confirm(HumanPress(proposal.id))
    assert proposal.outcome is Outcome.EXPIRED
    assert list(tmp_path.iterdir()) == []


def test_press_just_before_expiry_still_counts(gate, clock):
    proposal = gate.propose_create_file("note.txt", "hello")
    clock.now += EXPIRY_SECONDS - 0.1
    assert gate.confirm(HumanPress(proposal.id)).outcome is Outcome.CREATED


def test_confirming_twice_acts_once(gate):
    proposal = gate.propose_create_file("note.txt", "hello")
    gate.confirm(HumanPress(proposal.id))
    with pytest.raises(LookupError):
        gate.confirm(HumanPress(proposal.id))


def test_a_press_for_another_proposal_does_nothing(gate, tmp_path):
    gate.propose_create_file("note.txt", "hello")
    with pytest.raises(LookupError):
        gate.confirm(HumanPress("some-other-id"))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("forged", [None, True, "yes", {"proposal_id": "x"}])
def test_only_a_human_press_object_can_confirm(gate, forged):
    gate.propose_create_file("note.txt", "hello")
    with pytest.raises(TypeError):
        gate.confirm(forged)


def test_only_one_proposal_waits_at_a_time(gate):
    gate.propose_create_file("one.txt", "1")
    with pytest.raises(ProposalRefused):
        gate.propose_create_file("two.txt", "2")


def test_an_expired_proposal_makes_room(gate, clock):
    gate.propose_create_file("one.txt", "1")
    clock.now += EXPIRY_SECONDS + 1
    assert gate.propose_create_file("two.txt", "2").file_name == "two.txt"


def test_existing_files_are_never_overwritten(gate, tmp_path):
    (tmp_path / "note.txt").write_text("precious")
    with pytest.raises(ProposalRefused):
        gate.propose_create_file("note.txt", "new")


def test_a_file_that_appears_after_the_proposal_is_not_replaced(gate, tmp_path):
    proposal = gate.propose_create_file("note.txt", "new")
    (tmp_path / "note.txt").write_text("precious")
    done = gate.confirm(HumanPress(proposal.id))
    assert done.outcome is Outcome.FAILED
    assert done.as_result()["created"] is False
    assert (tmp_path / "note.txt").read_text() == "precious"


@pytest.mark.parametrize(
    "name",
    [
        "",
        "../evil.txt",
        "..\\evil.txt",
        "C:\\Windows\\evil.txt",
        "/etc/evil.txt",
        "sub/evil.txt",
        "evil.txt:stream",
        "evil.exe",
        "evil.bat",
        "evil.ps1",
        "evil.txt.",
        "evil.txt ",
        "noextension",
        "CON.txt",
        "nul.log",
        "a..b.txt",
        ".hidden.txt",
        "a" * 200 + ".txt",
        None,
        123,
        "bad\nname.txt",
        "%APPDATA%.txt",
        "name;rm.txt",
    ],
)
def test_unsafe_names_are_refused(name):
    with pytest.raises(ProposalRefused):
        validate_file_name(name)


@pytest.mark.parametrize("name", ["notes.txt", "My notes 2.md", "data_1.csv", "a-b.json"])
def test_ordinary_names_are_accepted(name):
    assert validate_file_name(name) == name


def test_oversized_and_non_text_content_is_refused(gate):
    with pytest.raises(ProposalRefused):
        gate.propose_create_file("big.txt", "x" * 100_001)
    with pytest.raises(ProposalRefused):
        gate.propose_create_file("x.txt", None)
