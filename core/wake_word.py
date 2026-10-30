"""
Local wake-word detection for ALPHA ("Hey Jarvis").

Design goals:
  • ZERO cost when the feature is off — openwakeword is imported ONLY inside
    start()/install helpers, never at module load. If the user never enables
    wake word, none of this touches the app.
  • ZERO latency on the audio path — the microphone callback only ever does a
    cheap, non-blocking queue push (feed()); the actual model inference runs in
    core/wake_worker.py, a separate process, so the real-time audio thread and
    the Gemini stream are never slowed and a native crash cannot take the app.
  • Fully local & offline — audio fed here never leaves the machine; there is no
    network call except the one-time model download the user triggers from the UI.

openwakeword ships small ONNX models (a few MB each) and runs comfortably on a
CPU. The pretrained wake phrase used here is "Hey Jarvis".
"""
from __future__ import annotations

import queue
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable

# Pretrained openwakeword model that listens for "Hey Jarvis".
WAKE_MODEL = "hey_jarvis"
# Score in [0,1]; above this counts as a detection. Tunable per environment.
DEFAULT_THRESHOLD = 0.5
# Mic frames arrive at 16 kHz int16; this is just the detector's input rate.
SAMPLE_RATE = 16000


def is_installed() -> bool:
    """True if the openwakeword package is importable (no model check)."""
    try:
        import importlib.util
        return importlib.util.find_spec("openwakeword") is not None
    except Exception:
        return False


def is_ready() -> bool:
    """True if openwakeword is installed AND its model files are present on disk.

    This is a cheap, DETERMINISTIC file-existence check. It deliberately does NOT
    construct a Model to probe readiness — doing that is slow and, worse, can clash
    with the detector's own Model when it's already running, which intermittently
    returned False and made the UI flicker to 'not downloaded'. Never raises.

    It must not import the package either. The UI calls this on its own thread at
    startup, and importing openwakeword there while start() imports it on the
    session thread hands one of them a half-initialised module, so the detector
    fails to load only when the whole app is running.
    """
    try:
        import importlib.util
        spec = importlib.util.find_spec("openwakeword")
        if spec is None or not spec.origin:
            return False
        models_dir = Path(spec.origin).resolve().parent / "resources" / "models"
        if not models_dir.is_dir():
            return False
        has_wake = (any(models_dir.glob(f"{WAKE_MODEL}*.onnx"))
                    or any(models_dir.glob(f"{WAKE_MODEL}*.tflite")))
        has_mel = (any(models_dir.glob("melspectrogram*.onnx"))
                   or any(models_dir.glob("melspectrogram*.tflite")))
        has_emb = (any(models_dir.glob("embedding_model*.onnx"))
                   or any(models_dir.glob("embedding_model*.tflite")))
        return bool(has_wake and has_mel and has_emb)
    except Exception:
        return False


def install_and_download(logger: Callable[[str], None] = print,
                         notify: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """
    One-click setup for the UI button: pip-install openwakeword if missing, then
    download the wake model. Returns (ok, message). Never raises — every failure
    is reported through the returned message and the logger.
    """
    _tell = notify or (lambda _msg: None)
    try:
        if not is_installed():
            logger("Wake word: installing openwakeword (one-time)…")
            _tell("Wake word: installing openwakeword (one-time)…")
            r = subprocess.run(
                [sys.executable, "-m", "pip", "install", "openwakeword"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if r.returncode != 0:
                tail = (r.stderr or r.stdout or "").strip().splitlines()[-1:] or [""]
                return False, f"pip install failed: {tail[0][:160]}"
        # Download the pretrained melspectrogram/embedding + wake models.
        logger("Wake word: downloading models…")
        _tell("Wake word: downloading models…")
        try:
            import openwakeword.utils as _u
            try:
                _u.download_models([WAKE_MODEL])
            except TypeError:
                _u.download_models()   # older signature downloads the default set
        except Exception as e:
            return False, f"model download failed: {e}"

        if not is_ready():
            return False, "installed, but the wake model could not be loaded."
        logger("Wake word: ready.")
        return True, "Wake word installed and ready."
    except Exception as e:
        return False, f"setup error: {e}"


class WakeWordDetector:
    """
    Talks to the wake model in core/wake_worker.py. The mic thread calls feed()
    with raw int16 frames; detections invoke on_detect() (called from a reader
    thread — the callback must marshal to whatever loop/UI it needs).

    start() returns as soon as the process is launched and `ready` turns True
    when the model has loaded. Until then frames are dropped, so stale audio is
    never scored. A worker that dies is restarted MAX_RESTARTS times, after which
    the wake word is reported unavailable and the rest of the app carries on.
    """

    MAX_RESTARTS = 2

    def __init__(self, on_detect: Callable[[], None],
                 threshold: float = DEFAULT_THRESHOLD,
                 logger: Callable[[str], None] = print,
                 notify: Callable[[str], None] | None = None):
        self._on_detect = on_detect
        self._threshold = threshold
        self._logger    = logger
        # See PluginRegistry: `logger` is the console and gets everything,
        # `notify` is the activity log and gets only what the user must act on.
        self._notify    = notify or (lambda _msg: None)
        self._queue: queue.Queue = queue.Queue(maxsize=50)
        self._proc: subprocess.Popen | None = None
        self._running = False
        self._ready = False
        self._restarts = 0
        self._last_error = ""

    def _command(self) -> list:
        return [sys.executable, "-m", "core.wake_worker", WAKE_MODEL, str(self._threshold)]

    def start(self) -> bool:
        """Launch the worker. True if launched; `ready` follows once it has loaded.
        Safe to call again — a no-op if already running. Never raises."""
        if self._running:
            return True
        self._running = True
        self._restarts = 0
        self._drain()
        if not self._spawn():
            return False
        threading.Thread(target=self._write_loop, daemon=True, name="WakeWordWriter").start()
        return True

    def _spawn(self) -> bool:
        try:
            self._proc = subprocess.Popen(
                self._command(),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
        except Exception as e:
            self._give_up(str(e))
            return False
        threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True,
                         name="WakeWordReader").start()
        return True

    def _give_up(self, reason: str) -> None:
        self._running = False
        self._ready = False
        self._logger(f"Wake word: could not run — {reason}")
        self._notify(f"Wake word unavailable ({reason[:120]}) — use the WAKE NOW button.")

    def stop(self) -> None:
        self._running = False
        self._ready = False
        # unblock the writer if it's waiting on the queue
        try:
            self._queue.put_nowait(None)
        except Exception:
            pass
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def running(self) -> bool:
        """Loading or listening. False once it has been stopped or given up."""
        return self._running

    def feed(self, frame_int16) -> None:
        """Called from the mic callback (real-time thread). Must stay cheap and
        never block — the frame is copied and dropped if the queue is backed up."""
        if not self._ready:
            return
        try:
            # frame_int16 is a numpy int16 array (possibly 2-D mono) — flatten to 1-D
            data = frame_int16[:, 0].copy() if getattr(frame_int16, "ndim", 1) > 1 else frame_int16.copy()
            self._queue.put_nowait(data)
        except queue.Full:
            pass
        except Exception:
            pass

    def _write_loop(self) -> None:
        while self._running:
            frame = self._queue.get()
            if frame is None or not self._running:
                break
            proc = self._proc
            if proc is None or not self._ready:
                continue
            try:
                proc.stdin.write(frame.tobytes())
            except (OSError, ValueError):
                pass            # the reader sees the same death and handles it

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for raw in proc.stdout:
            word = raw.decode("utf-8", "replace").strip()
            if word == "READY":
                self._ready = True
                self._logger("Wake word: listening.")
            elif word == "DETECT":
                # drain any backlog so we don't double-fire on the same utterance
                self._drain()
                try:
                    self._on_detect()
                except Exception as e:
                    self._logger(f"Wake word: on_detect error — {e}")
            elif word.startswith("ERROR"):
                self._last_error = word[5:].strip()

        code = proc.wait()
        if proc is not self._proc or not self._running:
            return              # stopped on purpose
        self._ready = False
        reason = self._last_error or f"the detector stopped, exit code {code & 0xFFFFFFFF:#x}"
        if self._restarts < self.MAX_RESTARTS:
            self._restarts += 1
            self._logger(f"Wake word: {reason}; restarting ({self._restarts}/{self.MAX_RESTARTS})")
            self._spawn()
        else:
            self._give_up(reason)

    def _drain(self) -> None:
        try:
            while True:
                self._queue.get_nowait()
        except Exception:
            pass
