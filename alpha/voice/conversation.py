"""Runs a conversation: connection, turns, tool requests and the current state.

All the logic here runs on one background thread with its own asyncio loop.
Anything arriving from the interface or the sound threads hops onto that loop
first, and results reach the interface through Qt signals.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Awaitable, Callable

from PySide6.QtCore import QObject, Signal

from datetime import datetime

from .. import keystore, languages, profile
from ..characters import CHARACTERS
from ..attachments import Attachment
from ..actions import ActionGate, HumanPress, Proposal, ProposalRefused
from ..audio.hub import AudioHub
from ..audio.microphone import MicState, Microphone
from ..audio.signal import level_of
from ..audio.speaker import Speaker
from ..settings import Settings
from ..memory import MemoryStore
from ..state import State
from ..professor.professor_mode import is_enter, is_for_aplha
from ..tools import catalogue
from ..tools.computer import skip_ad
from ..tools.registry import ToolBox, ToolRefused
from . import gemini, speech_model, wake_model
from .events import (
    EndingSoon,
    HeardText,
    LiveSession,
    ReplyAudio,
    ReplyDone,
    ReplyInterrupted,
    ReplyText,
    ToolRequest,
)
from .hands_free import Mode, SpeechTracker, WakeProcess
from .offline_speech import Narrator, Transcriber, take_sentences
from .persona import BRIEFING_REQUEST, FILE_TOOL

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 15.0
REPLY_TIMEOUT_SECONDS = 30.0
QUIT_DELAY_SECONDS = 4.0
ERROR_HOLD_SECONDS = 5.0
TRANSCRIBE_TIMEOUT_SECONDS = 20.0
FOLLOW_UP_SECONDS = 8.0
FOLLOW_UP_SETTLE_SECONDS = 0.7
FOLLOW_UP_SPEECH_CHUNKS = 3
DIRECT_SPEECH_CHUNKS = 6
LEAD_IN_CHUNKS = 6
OWN_VOICE_SECONDS = 1.5
PICTURE_COMING = {"ok": True, "detail": "The picture comes in the next message. Say nothing about it until then."}
PICTURE_MESSAGE = "This is the picture I just shared. Describe only what is in it."

Connector = Callable[[str, str], Awaitable[LiveSession]]

SOURCE_NAMES = {"screen": "your screen", "camera": "your camera"}
PICTURE_TIMEOUT_SECONDS = 12


def take_picture(source: str) -> bytes:
    """Runs `alpha.vision_worker`, which exits and releases the device straight after."""
    finished = subprocess.run(
        [sys.executable, "-m", "alpha.vision_worker", source],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        capture_output=True,
        timeout=PICTURE_TIMEOUT_SECONDS,
        creationflags=0x08000000,
    )
    if finished.returncode != 0 or not finished.stdout:
        raise RuntimeError(f"no picture came from {SOURCE_NAMES[source]}")
    return finished.stdout


def describe_failure(error: BaseException, api_key: str = "") -> str:
    """Turns a technical failure into a plain sentence. Never contains the key."""
    text = str(error)
    lowered = text.lower()
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)):
        message = "Gemini did not respond in time. Check your internet connection."
    elif "api key" in lowered or "api_key" in lowered or "unauthenticated" in lowered or "permission" in lowered:
        message = "Gemini did not accept the key. Check it in Settings."
    elif "not found" in lowered or "not supported" in lowered:
        message = "Gemini does not offer that model to this key. Check the model name in Settings."
    elif "quota" in lowered or "resource_exhausted" in lowered or "429" in lowered:
        message = "Gemini says the usage limit has been reached. Try again later."
    elif isinstance(error, OSError):
        message = "Could not reach Gemini. Check your internet connection."
    else:
        message = f"The connection to Gemini failed ({type(error).__name__}: {text[:160]})."
    return message.replace(api_key, "[key]") if api_key else message


class Conversation(QObject):
    state_changed = Signal(object, str)
    heard = Signal(str)
    replied = Signal(str)
    reply_finished = Signal()
    notice = Signal(str)
    mic_changed = Signal(object)
    proposal_ready = Signal(object)
    proposal_resolved = Signal(object)
    key_checked = Signal(bool, str)
    hands_free_changed = Signal(object)
    vision_changed = Signal(object)
    attachments_changed = Signal(object)
    watch_found = Signal(object)
    quit_requested = Signal()
    reminder_due = Signal(str)
    link_changed = Signal(bool)
    class_requested = Signal()
    class_message = Signal(str)

    def __init__(
        self,
        settings: Settings,
        hub: AudioHub,
        gate: ActionGate | None = None,
        connector: Connector | None = None,
        load_key: Callable[[], str] = keystore.load_key,
        wake_factory=None,
        speech_factory=None,
        speech_model_ready: Callable[[], bool] = speech_model.is_ready,
        toolbox: ToolBox | None = None,
        memory: MemoryStore | None = None,
        capture: Callable[[str], bytes] | None = None,
        wake_model_ready: Callable[[], bool] = wake_model.is_ready,
        backup=None,
    ):
        super().__init__()
        self._settings = settings
        self._gate = gate or ActionGate()
        self._connector = connector or self._connect_gemini
        self._load_key = load_key
        self._session: LiveSession | None = None
        self._session_ending = False
        self._connecting: asyncio.Lock | None = None
        self._receiver: asyncio.Task | None = None
        self._speech: asyncio.Queue | None = None
        self._talk_task: asyncio.Task | None = None
        self._mic_gave_error = False
        self._state = State.IDLE
        self._key_present = False
        self._playing = False
        self._own_voice_gone_at = 0.0
        self._awaiting_reply = False
        self._reply_timer: asyncio.TimerHandle | None = None
        self._error_timer: asyncio.TimerHandle | None = None
        self._tool_calls: dict[str, tuple[str, str]] = {}
        self.memory = memory or MemoryStore()
        self._tools = toolbox
        self._capture = capture or take_picture
        self._vision: set[str] = set()
        self._attachments: list[Attachment] = []
        self._no_voice_for = ""
        self._silenced = False
        self._backup = backup
        self._unanswered = ""
        self._in_class = False
        self._tool_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="alpha-tools")
        self._wake_factory = wake_factory or (
            lambda ready, wake, gone: WakeProcess(wake_model.model_dir(), ready, wake, gone)
        )
        self._wake_model_ready = wake_model_ready
        self._speech_factory = speech_factory or self._real_speech
        self._speech_model_ready = speech_model_ready
        self._transcriber: Transcriber | None = None
        self._narrator: Narrator | None = None
        self._heard_text: asyncio.Future | None = None
        self._reply_ended = False
        self._unsaid = ""
        self._wake: WakeProcess | None = None
        self._mode = Mode.OFF
        self._tracker = SpeechTracker()
        self._lead_in: deque[bytes] = deque(maxlen=LEAD_IN_CHUNKS)
        self._follow_up_timer: asyncio.TimerHandle | None = None
        self._follow_up_speech = 0
        self._follow_up_settled_at = 0.0
        self.mic = Microphone(
            hub,
            lambda: self._settings.microphone,
            lambda chunk: self._hop(self._queue_speech, chunk),
            lambda state: self._hop(self._mic_state, state),
            lambda text: self._hop(self._mic_error, text),
        )
        self.speaker = Speaker(
            hub,
            lambda: self._settings.speakers,
            lambda: self._hop(self._playback_started),
            lambda: self._hop(self._playback_finished),
            lambda text: self._hop(self._fail, text),
        )
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, name="alpha-conversation", daemon=True)

    @property
    def _offline(self) -> bool:
        """During class the words go to the professor, so speech is handled on this PC."""
        return self._settings.speech_engine == "offline" or self._in_class

    @property
    def in_class(self) -> bool:
        return self._in_class

    async def _connect_gemini(self, key: str, model: str) -> LiveSession:
        settings = self._settings
        return await gemini.connect(key, model, settings.character, settings.speech_language, profile.for_the_model())

    def _real_speech(self, language, on_text, on_sound, on_idle, on_exit):
        return (
            Transcriber(speech_model.model_dir(), language, on_text, on_exit),
            Narrator(on_sound, on_idle, on_exit),
        )

    def start(self) -> None:
        self._thread.start()
        self._hop(self._begin)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # An unclosed loop on Windows can crash Python when it is cleaned up later.
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            if tasks:
                self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self._loop.close()

    def _hop(self, function, *args) -> None:
        try:
            self._loop.call_soon_threadsafe(function, *args)
        except RuntimeError:
            pass  # Already shut down.

    def _spawn(self, coroutine) -> None:
        asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    @property
    def gate(self) -> ActionGate:
        return self._gate

    def _begin(self) -> None:
        self._connecting = asyncio.Lock()
        self._speech = asyncio.Queue()
        self._refresh_key()
        self._go_to_rest()
        self._prepare_offline_speech()

    # Called from the interface.

    def settings_changed(self) -> None:
        self._hop(self._apply_new_settings)

    def _apply_new_settings(self) -> None:
        self._refresh_key()
        self._prepare_offline_speech()
        self._spawn(self._drop_session())
        if self._state in (State.IDLE, State.WARNING):
            self._go_to_rest()

    def send_text(self, text: str) -> None:
        self._hop(self._begin_text, text)

    def set_class(self, on: bool) -> None:
        self._hop(self._change_class, on)

    def _change_class(self, on: bool) -> None:
        self._in_class = on
        self._prepare_offline_speech()

    def say_aloud(self, text: str) -> None:
        self._hop(self._say_aloud, text)

    def _say_aloud(self, text: str) -> None:
        """For the professor's one spoken line. The rest of a reply is only shown."""
        if self._narrator is None:
            return
        self._reply_ended = False
        self._unsaid = ""
        self._offline_reply_text(text)
        self._finish_offline_reply()

    def _went_to_class(self, text: str) -> bool:
        """Text from any source passes here, so typing and speaking behave the same."""
        if self._in_class and not is_for_aplha(text):
            self.class_message.emit(text)
        elif not self._in_class and is_enter(text):
            self.class_requested.emit()
        else:
            return False
        self._unanswered = ""
        self._stop_waiting_for_reply()
        if self._mode is Mode.REPLYING:
            self._open_follow_up()
        self._go_to_rest()
        return True

    def talk_pressed(self) -> None:
        self._hop(self._begin_talk)

    def talk_released(self) -> None:
        self._hop(self._end_talk)

    @property
    def is_replying(self) -> bool:
        return self._playing or self._awaiting_reply

    def interrupt(self) -> None:
        self._hop(self._interrupt)

    def _interrupt(self) -> None:
        """Gemini cannot be told to stop talking, so the rest of this reply is thrown away as it arrives."""
        if not (self._playing or self._awaiting_reply):
            return
        log.info("The owner interrupted the reply")
        self._silenced = True
        self._unanswered = ""
        self._hush()
        self._stop_waiting_for_reply()
        self.reply_finished.emit()
        self.notice.emit("Stopped.")
        if self._mode is Mode.REPLYING:
            self._open_follow_up()
        self._go_to_rest()

    def check_watches(self) -> None:
        try:
            self._tool_pool.submit(self._check_watches_on_tools_thread)
        except RuntimeError:
            log.info("Skipped checking the watched pages, because APLHA is closing")

    def _check_watches_on_tools_thread(self) -> None:
        board = self._toolbox().board
        if board is None:
            return
        try:
            for found in board.check_all():
                self.watch_found.emit(found)
        except Exception:
            log.exception("Checking the watched pages failed")

    def check_for_ads(self) -> None:
        try:
            self._tool_pool.submit(self._check_for_ads_on_tools_thread)
        except RuntimeError:
            log.info("Skipped checking for ads, because APLHA is closing")

    def _check_for_ads_on_tools_thread(self) -> None:
        computer = self._toolbox().computer
        try:
            if computer is not None and skip_ad(computer):
                log.info("Pressed the Skip button on an ad")
        except Exception:
            log.exception("Checking for an ad failed")

    def check_reminders(self) -> None:
        try:
            self._tool_pool.submit(self._check_reminders_on_tools_thread)
        except RuntimeError:
            log.info("Skipped checking reminders, because APLHA is closing")

    def _check_reminders_on_tools_thread(self) -> None:
        book = self._toolbox().reminders
        if book is None:
            return
        try:
            for reminder in book.take_due(datetime.now()):
                self.reminder_due.emit(reminder.text)
                self.send_text(f"Tell your owner this reminder they set, in one short sentence: {reminder.text}")
        except Exception:
            log.exception("Checking reminders failed")

    def give_briefing(self) -> None:
        self.send_text(BRIEFING_REQUEST)

    def undo(self) -> None:
        self._tool_pool.submit(self._undo_on_tools_thread)

    def _undo_on_tools_thread(self) -> None:
        try:
            self.notice.emit(self._toolbox().undo.undo())
        except ToolRefused as error:
            self.notice.emit(str(error))
        except Exception:
            log.exception("Undo failed")
            self.notice.emit("That could not be undone.")

    def set_vision(self, source: str, wanted: bool) -> None:
        self._hop(self._change_vision, source, wanted)

    def _change_vision(self, source: str, wanted: bool) -> None:
        if wanted:
            self._vision.add(source)
        else:
            self._vision.discard(source)
        log.info("Sharing is now: %s", sorted(self._vision) or "off")
        self.vision_changed.emit(set(self._vision))

    def add_attachment(self, item: Attachment) -> None:
        self._hop(self._add_attachment, item)

    def clear_attachments(self) -> None:
        self._hop(self._clear_attachments)

    def _add_attachment(self, item: Attachment) -> None:
        self._attachments.append(item)
        self.attachments_changed.emit(list(self._attachments))

    def _clear_attachments(self) -> None:
        self._attachments = []
        self.attachments_changed.emit([])

    async def _send_attachments(self, session: LiveSession) -> None:
        """Chosen files go with the next message, once, and only then."""
        items, self._attachments = self._attachments, []
        if items:
            self.attachments_changed.emit([])
        for item in items:
            if item.is_photo:
                await session.send_image(item.photo)
            else:
                await session.send_note(item.as_note())
            self.notice.emit(f"Sent {item.name} to Gemini.")

    async def _send_pictures(self, session: LiveSession) -> None:
        """One picture per turn, only for what is switched on at this moment."""
        await self._send_attachments(session)
        for source in sorted(self._vision):
            try:
                picture = await asyncio.to_thread(self._capture, source)
                await session.send_image(picture)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                self._vision.discard(source)
                self.vision_changed.emit(set(self._vision))
                self.notice.emit(f"Sharing {SOURCE_NAMES[source]} stopped: {error}")
            else:
                self.notice.emit(f"Sent a picture of {SOURCE_NAMES[source]} to Gemini.")

    def set_hands_free(self, wanted: bool) -> None:
        self._hop(self._change_hands_free, wanted)

    def propose_file(self, file_name: str, content: str) -> None:
        """The user's own /file command. It goes through the same gate as the model."""
        self._hop(self._local_proposal, file_name, content)

    def confirm(self, press: HumanPress) -> None:
        self._hop(self._confirm, press)

    def cancel(self, proposal_id: str) -> None:
        self._hop(self._cancel, proposal_id)

    def check_key(self, api_key: str, model: str) -> None:
        self._spawn(self._check_key(api_key, model))

    def shutdown(self) -> None:
        self._hop(self._change_hands_free, False)
        self._hop(self._stop_offline_speech)
        self.mic.release()
        self.speaker.stop_now()
        self.memory.close()
        if not self._thread.is_alive():
            return
        try:
            asyncio.run_coroutine_threadsafe(self._drop_session(), self._loop).result(timeout=3)
        except Exception:
            log.warning("The session did not close in time")
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=2)
        self._tool_pool.shutdown(wait=False, cancel_futures=True)

    # State.

    def _set_state(self, state: State, detail: str = "") -> None:
        self._state = state
        self.state_changed.emit(state, detail)

    def _refresh_key(self) -> None:
        try:
            self._key_present = bool(self._load_key())
        except keystore.KeyStoreError as error:
            self._key_present = False
            self.notice.emit(str(error))

    def _go_to_rest(self) -> None:
        if self._gate.current is not None:
            self._set_state(State.WARNING, "Waiting for your decision")
        elif not self._key_present:
            self._set_state(State.WARNING, "Add your Gemini key in Settings")
        elif self._mode is Mode.FOLLOW_UP:
            self._set_state(State.IDLE, "Go ahead, I am listening")
        elif self._mode is not Mode.OFF:
            self._set_state(State.IDLE, "Just start talking")
        else:
            self._set_state(State.IDLE)

    def _fail(self, text: str) -> None:
        log.warning("Failure shown to user: %s", text)
        self._stop_waiting_for_reply()
        self._set_state(State.ERROR, text)
        self.notice.emit(text)
        if self._error_timer is not None:
            self._error_timer.cancel()
        self._error_timer = self._loop.call_later(ERROR_HOLD_SECONDS, self._leave_error)

    def _leave_error(self) -> None:
        self._error_timer = None
        if self._state is State.ERROR:
            self._go_to_rest()

    def _expect_reply(self) -> None:
        self._stop_waiting_for_reply()
        self._awaiting_reply = True
        self._set_state(State.THINKING)
        self._reply_timer = self._loop.call_later(REPLY_TIMEOUT_SECONDS, self._reply_overdue)

    def _stop_waiting_for_reply(self) -> None:
        self._awaiting_reply = False
        if self._reply_timer is not None:
            self._reply_timer.cancel()
            self._reply_timer = None

    def _reply_overdue(self) -> None:
        self._reply_timer = None
        self._spawn(self._turn_to_backup("Gemini did not reply in time."))

    async def _turn_to_backup(self, reason: str) -> None:
        """The second AI gets only the words of an unanswered message, and can answer but never act."""
        question, self._unanswered = self._unanswered, ""
        if not question or self._backup is None or not self._backup.connected():
            self._fail(reason)
            return
        self._stop_waiting_for_reply()
        self._awaiting_reply = True
        self._set_state(State.THINKING)
        self.notice.emit(f"{reason} Asking {self._backup.name} instead.")
        try:
            answer = await self._loop.run_in_executor(self._tool_pool, self._backup.stand_in, question)
        except Exception as error:
            log.warning("The second AI could not stand in: %s", error)
            self._fail(reason)
            return
        if not self._awaiting_reply:
            return
        self._stop_waiting_for_reply()
        self.replied.emit(answer)
        self.reply_finished.emit()
        if self._offline and self._narrator is not None:
            self._reply_ended = False
            self._offline_reply_text(answer)
            self._finish_offline_reply()
        else:
            self._go_to_rest()

    # Connection.

    async def _session_ready(self) -> LiveSession:
        async with self._connecting:
            if self._session is not None and self._session_ending:
                await self._drop_session()
            if self._session is None:
                key = self._load_key()
                if not key:
                    raise PermissionError("api key missing")
                self._session = await asyncio.wait_for(
                    self._connector(key, self._settings.model), CONNECT_TIMEOUT_SECONDS
                )
                self._session_ending = False
                self.link_changed.emit(True)
                self._receiver = self._loop.create_task(self._receive(self._session))
            return self._session

    async def _drop_session(self) -> None:
        session, self._session = self._session, None
        if session is not None:
            self.link_changed.emit(False)
        receiver, self._receiver = self._receiver, None
        self._session_ending = False
        self._tool_calls.clear()
        if receiver is not None:
            receiver.cancel()
        if session is not None:
            try:
                await session.close()
            except Exception:
                log.exception("The session did not close cleanly")

    async def _receive(self, session: LiveSession) -> None:
        try:
            async for event in session.events():
                self._on_event(event)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if self._session is session:
                self._session = None
                self.link_changed.emit(False)
                self._lost_session(error)

    def _lost_session(self, error: Exception) -> None:
        log.warning("The live session ended: %s", describe_failure(error, self._safe_key()))
        self._tool_calls.clear()
        if self._awaiting_reply or self._playing:
            self.speaker.reply_complete()
            self._spawn(self._turn_to_backup(describe_failure(error, self._safe_key())))
        else:
            self.notice.emit("The live session ended. The next message starts a fresh one.")

    def _safe_key(self) -> str:
        try:
            return self._load_key()
        except keystore.KeyStoreError:
            return ""

    # Text turns.

    def _begin_text(self, text: str) -> None:
        if not self._key_present:
            self._fail("Add your Gemini key in Settings before sending a message.")
            return
        self._silenced = False
        self._expect_reply()
        self._spawn(self._send_text(text))

    async def _send_text(self, text: str) -> None:
        if self._went_to_class(text):
            return
        self._unanswered = text
        try:
            session = await self._session_ready()
            await self._send_pictures(session)
            await session.send_text(text)
        except Exception as error:
            await self._turn_to_backup(describe_failure(error, self._safe_key()))

    # Voice turns.

    def _begin_talk(self) -> None:
        if self._talk_task is not None and not self._talk_task.done():
            return
        if not self._key_present:
            self._fail("Add your Gemini key in Settings before talking.")
            return
        if self._mode is not Mode.OFF:
            self._start_hands_free_turn()
            return
        self._hush()
        self._stop_waiting_for_reply()
        self._silenced = False
        self._mic_gave_error = False
        while not self._speech.empty():
            self._speech.get_nowait()
        self.mic.press()
        self._talk_task = self._loop.create_task(self._talk())

    def _end_talk(self) -> None:
        if self._mode is Mode.TURN:
            self._finish_hands_free_turn()
        elif self._mode is Mode.OFF:
            self.mic.release()

    def _queue_speech(self, chunk: bytes) -> None:
        if self._mode is Mode.OFF:
            self._speech.put_nowait(chunk)
        else:
            self._hands_free_sound(chunk)

    def _mic_state(self, state: MicState) -> None:
        log.info("Microphone is now %s", state.value)
        self.mic_changed.emit(state)
        if self._mode is not Mode.OFF:
            return
        if state is MicState.OPEN:
            self._set_state(State.LISTENING)
        elif state is MicState.CLOSED:
            self._speech.put_nowait(None)

    def _mic_error(self, text: str) -> None:
        self._mic_gave_error = True
        self._change_hands_free(False)
        self._fail(text)

    # Hands-free listening. Sound only reaches Gemini in Mode.TURN.

    def _set_mode(self, mode: Mode) -> None:
        if mode is not self._mode:
            log.info("Hands-free is now %s", mode.value)
            self._mode = mode
            self.hands_free_changed.emit(mode)

    def _change_hands_free(self, wanted: bool) -> None:
        if wanted == (self._mode is not Mode.OFF):
            return
        if wanted:
            self._start_hands_free()
        else:
            self._stop_hands_free()

    def _start_hands_free(self) -> None:
        if not self._key_present:
            self._fail("Add your Gemini key in Settings before using hands-free.")
            self.hands_free_changed.emit(Mode.OFF)
            return
        if not self._wake_model_ready():
            self._fail("The offline speech model is missing. Run: python -m alpha.voice.wake_model")
            self.hands_free_changed.emit(Mode.OFF)
            return
        try:
            self._wake = self._wake_factory(
                lambda: self._hop(self._wake_ready),
                lambda: self._hop(self._heard_wake_word),
                lambda: self._hop(self._wake_died),
            )
            self._wake.on_decision = lambda word: self._hop(self._heard_decision, word)
        except OSError as error:
            self._fail(f"The wake word listener could not start: {error}")
            self.hands_free_changed.emit(Mode.OFF)
            return
        self._mic_gave_error = False
        self._lead_in.clear()
        self.speaker.stop_now()
        self._set_mode(Mode.WAITING)
        self.mic.press()
        self._go_to_rest()

    def _stop_hands_free(self) -> None:
        was_turn = self._mode is Mode.TURN
        self._cancel_follow_up()
        self._set_mode(Mode.OFF)
        if was_turn:
            self._speech.put_nowait(None)
        wake, self._wake = self._wake, None
        if wake is not None:
            threading.Thread(target=wake.stop, name="alpha-wake-stop", daemon=True).start()
        self.mic.release()
        if self._state is State.LISTENING or self._state is State.IDLE:
            self._go_to_rest()

    def _wake_ready(self) -> None:
        log.info("The wake word listener is ready")

    def _wake_died(self) -> None:
        if self._mode is not Mode.OFF:
            self._wake = None
            self._stop_hands_free()
            self._fail("The wake word listener stopped, so hands-free is off. The rest of APLHA is fine.")

    def _heard_wake_word(self) -> None:
        """A word this short is easily imagined in room noise, or in APLHA's own voice."""
        if self._playing or not self._someone_spoke():
            log.info("Ignored a wake word with no clear speech behind it")
            return
        log.info("Wake word heard")
        if self._mode in (Mode.WAITING, Mode.REPLYING, Mode.FOLLOW_UP) and self._gate.current is None:
            self._start_hands_free_turn()

    def _heard_decision(self, word: str) -> None:
        """Comes only from the local listener on the microphone, so nothing the model writes can reach
        it. The pause after speaking keeps APLHA's own voice from counting."""
        proposal = self._gate.current
        if proposal is None or self._playing or self._loop.time() < self._own_voice_gone_at:
            return
        if not self._someone_spoke():
            log.info("Ignored a decision word with no clear speech behind it")
            return
        log.info("Heard '%s' for the waiting action", word)
        if word == "confirm":
            self._confirm(HumanPress(proposal.id))
        else:
            self._cancel(proposal.id)

    def _someone_spoke(self) -> bool:
        clear = sum(self._tracker.is_clear_speech(level_of(chunk)) for chunk in self._lead_in)
        return clear >= FOLLOW_UP_SPEECH_CHUNKS

    def _start_hands_free_turn(self) -> None:
        if self._talk_task is not None and not self._talk_task.done():
            return
        self._cancel_follow_up()
        self._hush()
        self._stop_waiting_for_reply()
        self._silenced = False
        self._tracker.reset()
        while not self._speech.empty():
            self._speech.get_nowait()
        for chunk in self._lead_in:
            self._speech.put_nowait(chunk)
        self._lead_in.clear()
        self._set_mode(Mode.TURN)
        self._set_state(State.LISTENING)
        self._talk_task = self._loop.create_task(self._talk())

    def _finish_hands_free_turn(self) -> None:
        self._set_mode(Mode.REPLYING)
        self._speech.put_nowait(None)

    def _hands_free_sound(self, chunk: bytes) -> None:
        if self._mode is Mode.TURN:
            self._speech.put_nowait(chunk)
            if self._tracker.feed(level_of(chunk)):
                self._finish_hands_free_turn()
            return
        self._lead_in.append(chunk)
        if self._wake is not None:
            self._wake.feed(chunk)
        level = level_of(chunk)
        if self._mode is Mode.WAITING:
            self._tracker.hear_room(level)
        waiting_for_a_word = self._gate.current is not None
        if self._mode in (Mode.WAITING, Mode.FOLLOW_UP) and self._follow_up_heard(level) and not waiting_for_a_word:
            self._start_hands_free_turn()

    def _follow_up_heard(self, level: float) -> bool:
        """Waits for the room to settle, then for steady speech, so APLHA does not answer its own echo."""
        if self._loop.time() < self._follow_up_settled_at:
            self._follow_up_speech = 0
            return False
        # Brief dips between syllables count against it, but do not start it over.
        change = 1 if self._tracker.is_clear_speech(level) else -1
        self._follow_up_speech = max(0, self._follow_up_speech + change)
        needed = FOLLOW_UP_SPEECH_CHUNKS if self._mode is Mode.FOLLOW_UP else DIRECT_SPEECH_CHUNKS
        return self._follow_up_speech >= needed

    def _open_follow_up(self) -> None:
        self._set_mode(Mode.FOLLOW_UP)
        self._follow_up_speech = 0
        self._follow_up_settled_at = self._loop.time() + FOLLOW_UP_SETTLE_SECONDS
        self._follow_up_timer = self._loop.call_later(FOLLOW_UP_SECONDS, self._close_follow_up)

    def _close_follow_up(self) -> None:
        self._follow_up_timer = None
        if self._mode is Mode.FOLLOW_UP:
            self._set_mode(Mode.WAITING)
            self._go_to_rest()

    def _cancel_follow_up(self) -> None:
        if self._follow_up_timer is not None:
            self._follow_up_timer.cancel()
            self._follow_up_timer = None

    async def _talk(self) -> None:
        # Nothing reaches Gemini until there is sound, because it closes the
        # session if a turn is marked as started and ended with no audio in it.
        chunk = await self._speech.get()
        if chunk is None:
            if not self._mic_gave_error:
                self.notice.emit("No sound was captured. Hold the talk control a little longer.")
                self._go_to_rest()
            return
        if self._offline:
            await self._talk_offline(chunk)
            return
        try:
            session = await self._session_ready()
            await self._send_pictures(session)
            await session.start_speech()
            while chunk is not None:
                await session.send_speech(chunk)
                chunk = await self._speech.get()
            await session.end_speech()
        except Exception as error:
            if self._mode is Mode.OFF:
                self.mic.release()
            else:
                self._set_mode(Mode.WAITING)
            self._fail(describe_failure(error, self._safe_key()))
            return
        self._expect_reply()

    # Offline speech. Sound stays here, and only the words go to Gemini.

    def _prepare_offline_speech(self) -> None:
        language = self._settings.speech_language
        if not self._offline or (self._transcriber is not None and self._transcriber.language != language):
            self._stop_offline_speech()
        if self._offline and self._transcriber is None:
            if not self._speech_model_ready():
                self.notice.emit("The offline speech model is missing. Run: python -m alpha.voice.speech_model")
                return
            try:
                self._transcriber, self._narrator = self._speech_factory(
                    language,
                    lambda text: self._hop(self._heard_offline, text),
                    lambda sound: self._hop(self._offline_sound, sound),
                    lambda: self._hop(self._offline_voice_done),
                    lambda: self._hop(self._offline_speech_died),
                )
            except OSError as error:
                self._fail(f"Offline speech could not start: {error}")

    def _stop_offline_speech(self) -> None:
        transcriber, self._transcriber = self._transcriber, None
        narrator, self._narrator = self._narrator, None
        if transcriber is not None:
            transcriber.stop()
        if narrator is not None:
            narrator.stop()

    def _offline_speech_died(self) -> None:
        if self._transcriber is not None:
            self._stop_offline_speech()
            self._fail("Offline speech stopped. It will restart with your next message.")

    def _hush(self) -> None:
        self.speaker.stop_now()
        self._unsaid = ""
        if self._narrator is not None:
            self._narrator.clear()

    def _heard_offline(self, text: str) -> None:
        if self._heard_text is not None and not self._heard_text.done():
            self._heard_text.set_result(text)

    async def _talk_offline(self, chunk: bytes) -> None:
        self._prepare_offline_speech()
        if self._transcriber is None:
            self._end_failed_turn("Offline speech is not available.")
            return
        self._heard_text = self._loop.create_future()
        while chunk is not None:
            self._transcriber.feed(chunk)
            chunk = await self._speech.get()
        self._expect_reply()
        self._transcriber.finish()
        try:
            text = await asyncio.wait_for(self._heard_text, TRANSCRIBE_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            self._end_failed_turn("The offline listener took too long.")
            return
        if not text:
            self._stop_waiting_for_reply()
            self.notice.emit("I could not make out any words.")
            if self._mode is Mode.REPLYING:
                self._open_follow_up()
            self._go_to_rest()
            return
        self.heard.emit(text)
        await self._send_text(text)

    def _end_failed_turn(self, text: str) -> None:
        if self._mode is Mode.OFF:
            self.mic.release()
        else:
            self._set_mode(Mode.WAITING)
        self._fail(text)

    def _reply_language(self, text: str) -> str:
        chosen = self._settings.speech_language
        return languages.language_of(text) if chosen == languages.AUTO else chosen

    def _offline_voice(self, language: str) -> str | None:
        """Each character keeps its own English voice. Other languages use whatever Windows has."""
        if language == "en":
            return CHARACTERS[self._settings.character].windows_voice
        return languages.installed_voices().get(language)

    def _offline_reply_text(self, text: str) -> None:
        language = self._reply_language(text)
        voice = self._offline_voice(language)
        if voice is None:
            self._no_voice_for = language
            return
        self._narrator.voice = voice
        self._unsaid += text
        sentences, self._unsaid = take_sentences(self._unsaid)
        for sentence in sentences:
            self._narrator.say(sentence)

    def _offline_sound(self, sound: bytes) -> None:
        self.speaker.play(sound)
        if self._playing and self._state is State.THINKING:
            self._playback_started()

    def _offline_voice_done(self) -> None:
        if self._reply_ended:
            self.speaker.reply_complete()

    def _finish_offline_reply(self) -> None:
        self._reply_ended = True
        if self._no_voice_for:
            self.notice.emit(f"Windows has no {languages.NAMES[self._no_voice_for]} voice, so this reply is text only.")
            self._no_voice_for = ""
            self._unsaid = ""
            self._playback_finished()
            return
        if self._unsaid.strip():
            self._narrator.say(self._unsaid.strip())
        self._unsaid = ""
        if not self._narrator.busy:
            self.speaker.reply_complete()

    # Events from the session.

    def _on_event(self, event) -> None:
        self._unanswered = ""
        if self._silenced and self._drop_silenced(event):
            return
        speaking_here = self._offline and self._narrator is not None
        if isinstance(event, ReplyAudio):
            if speaking_here:
                return
            self.speaker.play(event.pcm24k)
            if self._playing and self._state is State.THINKING:
                # The speakers were still open from the last reply, so no start signal comes.
                self._playback_started()
        elif isinstance(event, ReplyText):
            self._reply_ended = False
            self.replied.emit(event.text)
            if speaking_here:
                self._offline_reply_text(event.text)
        elif isinstance(event, HeardText):
            self.heard.emit(event.text)
        elif isinstance(event, ReplyDone):
            self._stop_waiting_for_reply()
            self.reply_finished.emit()
            if speaking_here:
                self._finish_offline_reply()
            else:
                self.speaker.reply_complete()
        elif isinstance(event, ReplyInterrupted):
            self._hush()
        elif isinstance(event, ToolRequest):
            self._on_tool_request(event)
        elif isinstance(event, EndingSoon):
            self._session_ending = True

    def _playback_started(self) -> None:
        self._playing = True
        self._stop_waiting_for_reply()
        if self._state is not State.LISTENING:
            self._set_state(State.SPEAKING)

    def _playback_finished(self) -> None:
        self._playing = False
        self._own_voice_gone_at = self._loop.time() + OWN_VOICE_SECONDS
        if self._mode is Mode.REPLYING and not self._awaiting_reply:
            self._open_follow_up()
        if self._state not in (State.LISTENING, State.ERROR) and not self._awaiting_reply:
            self._go_to_rest()

    def _drop_silenced(self, event) -> bool:
        """What is left of a reply the owner stopped. A dropped tool call still needs an answer."""
        if isinstance(event, (ReplyDone, ReplyInterrupted)):
            self._silenced = False
            return True
        if isinstance(event, ToolRequest):
            self._answer_tool(event.call_id, event.name, {"ok": False, "detail": "The owner pressed stop."})
            return True
        return isinstance(event, (ReplyAudio, ReplyText))

    # The safety gate.

    def _on_tool_request(self, request: ToolRequest) -> None:
        # The model can only name a tool from the list. Deciding is not a tool.
        tool = self._toolbox().get(request.name)
        if tool is None:
            self._answer_tool(request.call_id, request.name, {"error": "That tool does not exist."})
            return
        if tool.name == catalogue.VIEW_TOOL:
            self._spawn(self._share_view(request))
            return
        self._tool_pool.submit(self._run_tool, tool, request)

    async def _share_view(self, request: ToolRequest) -> None:
        """The picture must follow the tool result. Sent before it, real Gemini never saw the picture
        and invented an answer, so it goes afterwards with a message of its own."""
        source, wanted = request.arguments.get("source"), request.arguments.get("on")
        if source not in SOURCE_NAMES or not isinstance(wanted, bool):
            self._tool_finished(request, {"ok": False, "detail": "source must be camera or screen, and on true or false."})
            return
        self._change_vision(source, wanted)
        if not wanted:
            self._tool_finished(request, {"ok": True, "detail": f"Stopped sharing {SOURCE_NAMES[source]}."})
            return
        try:
            picture = await asyncio.to_thread(self._capture, source)
            session = self._session
            await self._send_tool_result(request.call_id, request.name, PICTURE_COMING, False)
            await session.send_image(picture)
            self._expect_reply()
            await session.send_text(PICTURE_MESSAGE)
        except (AttributeError, RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            self._change_vision(source, False)
            self._tool_finished(request, {"ok": False, "detail": f"Could not use {SOURCE_NAMES[source]}: {error}"})
            return
        self.notice.emit(f"Sent a picture of {SOURCE_NAMES[source]} to Gemini.")

    def _toolbox(self) -> ToolBox:
        if self._tools is None:
            self._tools = catalogue.real_toolbox(self.memory)
        return self._tools

    def _run_tool(self, tool, request: ToolRequest) -> None:
        """Runs on the tools thread, so slow Windows calls never hold up the conversation."""
        arguments = request.arguments
        try:
            if tool.name == FILE_TOOL:
                done = self._gate.create_file_now(arguments.get("file_name"), arguments.get("content"))
                self._hop(self._tool_finished, request, done.as_result())
                return
            if tool.needs_confirmation:
                plan = tool.propose(arguments)
                if not tool.always_asks:
                    self._hop(self._tool_finished, request, {"ok": True, "completed": True, "detail": plan.perform()})
                    return
                proposal = self._gate.propose(plan.summary, plan.where, plan.preview, plan.perform)
            else:
                self._hop(self._tool_finished, request, tool.run(arguments))
                return
        except (ProposalRefused, ToolRefused) as error:
            result = {"ok": False, "detail": str(error)}
            if tool.name == FILE_TOOL:
                result["created"] = False
        except Exception as error:
            log.exception("Tool %s failed", tool.name)
            result = {"ok": False, "detail": f"That failed ({type(error).__name__})."}
        else:
            self._hop(self._tool_proposed, proposal, request)
            return
        self._hop(self._tool_finished, request, result)

    def _tool_proposed(self, proposal: Proposal, request: ToolRequest) -> None:
        self._tool_calls[proposal.id] = (request.call_id, request.name)
        self._present(proposal)

    def _tool_finished(self, request: ToolRequest, result: dict) -> None:
        log.info("Tool %s: %s", request.name, result.get("detail", ""))
        self.notice.emit(result.get("detail", ""))
        # The model may stay quiet after a result, which is not a failure.
        self._stop_waiting_for_reply()
        if self._state is State.THINKING:
            self._go_to_rest()
        self._answer_tool(request.call_id, request.name, result)
        if request.name == catalogue.QUIT_TOOL:
            self._loop.call_later(QUIT_DELAY_SECONDS, self.quit_requested.emit)
        if request.name == catalogue.CLASS_TOOL:
            self.class_requested.emit()

    def _local_proposal(self, file_name: str, content: str) -> None:
        try:
            proposal = self._gate.propose_create_file(file_name, content)
        except ProposalRefused as error:
            self.notice.emit(f"That file request was refused: {error}")
            return
        self._present(proposal)

    def _present(self, proposal: Proposal) -> None:
        log.info("Action to %s is waiting for a decision", proposal.summary)
        self._stop_waiting_for_reply()
        self.proposal_ready.emit(proposal)
        if not self._playing and self._state is not State.LISTENING:
            self._go_to_rest()
        self._loop.call_later(self._gate.expiry_seconds + 0.05, self._check_expiry)

    def _check_expiry(self) -> None:
        expired = self._gate.expire_if_due()
        if expired is not None:
            self._resolved(expired)

    def _confirm(self, press: HumanPress) -> None:
        self._tool_pool.submit(self._confirm_on_tools_thread, press)

    def _confirm_on_tools_thread(self, press: HumanPress) -> None:
        expired = self._gate.expire_if_due()
        if expired is not None:
            self._hop(self._resolved, expired)
            return
        try:
            proposal = self._gate.confirm(press)
        except LookupError:
            return
        self._hop(self._resolved, proposal)

    def _cancel(self, proposal_id: str) -> None:
        proposal = self._gate.cancel(proposal_id)
        if proposal is not None:
            self._resolved(proposal)

    def _resolved(self, proposal: Proposal) -> None:
        log.info("Action to %s ended: %s", proposal.summary, proposal.outcome.value)
        self.proposal_resolved.emit(proposal)
        self.notice.emit(proposal.detail)
        call = self._tool_calls.pop(proposal.id, None)
        if call is not None and self._session is not None:
            self._expect_reply()
            self._answer_tool(call[0], call[1], proposal.as_result(), later=True)
        elif self._state is State.WARNING:
            self._go_to_rest()

    def _answer_tool(self, call_id: str, name: str, result: dict, later: bool = False) -> None:
        self._spawn(self._send_tool_result(call_id, name, result, later))

    async def _send_tool_result(self, call_id: str, name: str, result: dict, later: bool) -> None:
        session = self._session
        if session is None:
            return
        try:
            await session.send_tool_result(call_id, name, result, later)
        except Exception as error:
            self._fail(describe_failure(error, self._safe_key()))

    # Settings.

    async def _check_key(self, api_key: str, model: str) -> None:
        """Opens and closes a real session, so success means Gemini said yes."""
        try:
            session = await asyncio.wait_for(self._connector(api_key, model), CONNECT_TIMEOUT_SECONDS)
            await session.close()
        except Exception as error:
            self.key_checked.emit(False, describe_failure(error, api_key))
            return
        self.key_checked.emit(True, "Gemini accepted the key.")
