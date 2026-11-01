"""The safety gate: what reaches it waits until a person presses CONFIRM.

Carrying out a proposal needs a `HumanPress`. Only two places make one:
`alpha.ui.confirm_card`, for a real press from the mouse or the owner's keyword typed
on the card, and `Conversation._heard_decision`, for "confirm" or the keyword heard by
the local listener on the microphone. Nothing the model writes reaches either. `Tool.always_asks` in
`alpha.tools.registry` decides which actions come here.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable

from . import paths

EXPIRY_SECONDS = 60.0
MAX_CONTENT_CHARS = 100_000
MAX_NAME_CHARS = 100
ALLOWED_SUFFIXES = (".txt", ".md", ".csv", ".json", ".log")

_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]*$")
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)} | {f"lpt{i}" for i in range(1, 10)}


class Outcome(str, Enum):
    PENDING = "pending"
    CREATED = "created"
    DONE = "done"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    FAILED = "failed"


class ProposalRefused(ValueError):
    """The proposal was not acceptable, so nothing was put in front of the user."""


class HumanPress:
    """Proof that a person decided one proposal: a real press, their word on the microphone, or a typed keyword."""

    def __init__(self, proposal_id: str):
        self.proposal_id = proposal_id


@dataclass
class Proposal:
    id: str
    summary: str
    where: str
    preview: str
    perform: Callable[[], str]
    expires_at: float
    success: Outcome = Outcome.DONE
    outcome: Outcome = Outcome.PENDING
    detail: str = ""
    file_name: str = ""
    target: Path | None = None
    owner_only: bool = False  # Confirm is not enough once a keyword is set. It takes the keyword, spoken or typed.

    @property
    def succeeded(self) -> bool:
        return self.outcome in (Outcome.CREATED, Outcome.DONE)

    def as_result(self) -> dict:
        result = {
            "action": self.summary,
            "outcome": self.outcome.value,
            "completed": self.succeeded,
            "detail": self.detail,
        }
        if self.file_name:
            result["file_name"] = self.file_name
            result["created"] = self.succeeded
        return result


def validate_file_name(name: str) -> str:
    if not isinstance(name, str):
        raise ProposalRefused("The file name must be text.")
    if not name or len(name) > MAX_NAME_CHARS:
        raise ProposalRefused(f"The file name must be 1 to {MAX_NAME_CHARS} characters.")
    if not _SAFE_NAME.match(name) or ".." in name or name.endswith((" ", ".")):
        raise ProposalRefused("The file name may only use letters, digits, spaces, dots, dashes and underscores.")
    path = Path(name)
    if path.suffix.lower() not in ALLOWED_SUFFIXES:
        raise ProposalRefused(f"Only these file types are allowed: {', '.join(ALLOWED_SUFFIXES)}.")
    if name.split(".")[0].strip().lower() in _RESERVED:
        raise ProposalRefused("Windows reserves that file name.")
    return name


def _write_new_file(target: Path, content: str) -> str:
    data = content.encode("utf-8")
    # "x" fails rather than replaces, even if the file appeared since the proposal.
    with open(target, "xb") as handle:
        handle.write(data)
    if target.read_bytes() != data:
        raise OSError("The file did not read back the same.")
    return f"Created {target} and read it back ({len(data)} bytes)."


class ActionGate:
    """Holds at most one proposal at a time. Safe to use from any thread."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        folder: Path | None = None,
        expiry_seconds: float = EXPIRY_SECONDS,
    ):
        self._clock = clock
        self._folder = folder
        self.expiry_seconds = expiry_seconds
        self._lock = threading.Lock()
        self._current: Proposal | None = None

    def propose(self, summary: str, where: str, preview: str, perform: Callable[[], str], owner_only: bool = False) -> Proposal:
        return self._hold(Proposal(uuid.uuid4().hex, summary, where, preview, perform, 0.0, owner_only=owner_only))

    def propose_create_file(self, file_name: str, content: str) -> Proposal:
        return self._hold(self._file_proposal(file_name, content))

    def create_file_now(self, file_name: str, content: str) -> Proposal:
        """Only ever makes a new file in APLHA's own folder, so it never replaces anything."""
        proposal = self._file_proposal(file_name, content)
        self._carry_out(proposal)
        return proposal

    def _file_proposal(self, file_name: str, content: str) -> Proposal:
        file_name = validate_file_name(file_name)
        if not isinstance(content, str):
            raise ProposalRefused("The file content must be text.")
        if len(content) > MAX_CONTENT_CHARS:
            raise ProposalRefused(f"The content is over the {MAX_CONTENT_CHARS} character limit.")
        folder = self._folder or paths.created_files_dir()
        target = folder / file_name
        if target.exists():
            raise ProposalRefused(f"{file_name} already exists, and APLHA does not overwrite files.")
        return Proposal(
            id=uuid.uuid4().hex,
            summary=f"create a file: {file_name}",
            where=f"It would be saved as {target}",
            preview=content,
            perform=lambda: _write_new_file(target, content),
            expires_at=0.0,
            success=Outcome.CREATED,
            file_name=file_name,
            target=target,
        )

    def _hold(self, proposal: Proposal) -> Proposal:
        with self._lock:
            self._expire_if_due()
            if self._current is not None:
                raise ProposalRefused("Another action is already waiting for a decision.")
            proposal.expires_at = self._clock() + self.expiry_seconds
            self._current = proposal
            return proposal

    @property
    def current(self) -> Proposal | None:
        with self._lock:
            return self._current

    def seconds_left(self, proposal: Proposal) -> float:
        return max(0.0, proposal.expires_at - self._clock())

    def confirm(self, press: HumanPress) -> Proposal:
        if not isinstance(press, HumanPress):
            raise TypeError("Only a press of the on-screen control can confirm.")
        with self._lock:
            self._expire_if_due()
            proposal = self._take(press.proposal_id)
            if proposal is None:
                raise LookupError("That action is no longer waiting for a decision.")
        self._carry_out(proposal)
        return proposal

    def cancel(self, proposal_id: str) -> Proposal | None:
        with self._lock:
            proposal = self._take(proposal_id)
            if proposal is not None:
                proposal.outcome = Outcome.CANCELLED
                proposal.detail = "The user cancelled, so nothing was done."
            return proposal

    def expire_if_due(self) -> Proposal | None:
        with self._lock:
            return self._expire_if_due()

    def _take(self, proposal_id: str) -> Proposal | None:
        if self._current is None or self._current.id != proposal_id:
            return None
        proposal, self._current = self._current, None
        return proposal

    def _expire_if_due(self) -> Proposal | None:
        proposal = self._current
        if proposal is None or self._clock() < proposal.expires_at:
            return None
        self._current = None
        proposal.outcome = Outcome.EXPIRED
        proposal.detail = "Nobody confirmed in time, so nothing was done."
        return proposal

    def _carry_out(self, proposal: Proposal) -> None:
        try:
            proposal.detail = proposal.perform()
        except Exception as error:
            proposal.outcome = Outcome.FAILED
            proposal.detail = f"It could not be done: {error}"
            return
        proposal.outcome = proposal.success
