"""
Behaviour tests for ALPHA. Standard library only, so they run in a fresh venv:

    .venv\\Scripts\\python -m unittest discover -s tests -v

Provider tests live in providers/_tests.py. Nothing here touches the network,
the microphone or real user data: memory tests use a temporary file.
"""
import json
import os
import platform
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from core import confirm, identity, state_theme, undo  # noqa: E402


class GateCase(unittest.TestCase):
    """Binds the confirmation gate to recorders, the way main.py binds the HUD."""

    def setUp(self):
        self.shown = []
        self.hidden = []
        confirm._pending = None
        confirm.bind(lambda t, d: self.shown.append((t, d)),
                     lambda: self.hidden.append(True))

    def tearDown(self):
        confirm._pending = None
        confirm.bind(None, None)

    def press(self, accepted, wait_for=None):
        confirm.resolve(accepted)
        deadline = time.time() + 3
        while wait_for is not None and not wait_for() and time.time() < deadline:
            time.sleep(0.01)


class ConfirmationGate(GateCase):
    def test_refuses_without_an_interface(self):
        confirm.bind(None, None)
        ran = []
        reply = confirm.request("k", "Do it", "", lambda: ran.append(1))
        self.assertEqual(ran, [])
        self.assertIn("have not done it", reply)

    def test_nothing_runs_before_the_user_answers(self):
        ran = []
        reply = confirm.request("k", "Do it", "detail", lambda: ran.append(1))
        self.assertEqual(ran, [])
        self.assertEqual(self.shown, [("Do it", "detail")])
        self.assertIn("CONFIRMATION_PENDING", reply)
        self.assertIn("Do not claim it is done", reply)

    def test_cancel_never_runs(self):
        ran = []
        confirm.request("k", "Do it", "", lambda: ran.append(1))
        confirm.resolve(False)
        time.sleep(0.05)
        self.assertEqual(ran, [])

    def test_confirm_runs_exactly_once(self):
        ran = []
        confirm.request("k", "Do it", "", lambda: ran.append(1))
        self.press(True, wait_for=lambda: ran)
        confirm.resolve(True)
        time.sleep(0.05)
        self.assertEqual(ran, [1])

    def test_expired_confirmation_does_not_run(self):
        ran = []
        confirm.request("k", "Do it", "", lambda: ran.append(1))
        confirm._pending.at -= confirm.TIMEOUT_SECONDS + 1
        confirm.resolve(True)
        time.sleep(0.05)
        self.assertEqual(ran, [])

    def test_a_waiting_banner_cannot_be_swapped(self):
        first, second = [], []
        confirm.request("a", "Harmless", "", lambda: first.append(1))
        reply = confirm.request("b", "Dangerous", "", lambda: second.append(1))
        self.assertIn("already waiting", reply)
        self.press(True, wait_for=lambda: first)
        self.assertEqual(first, [1])
        self.assertEqual(second, [])


class GeneratedCode(GateCase):
    def test_run_action_waits_for_a_person(self):
        from actions import code_helper
        with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
            path = f.name
        try:
            with mock.patch.object(code_helper, "_run_file", return_value="ok") as run:
                reply = code_helper._run_action(path, [], 5, None)
                run.assert_not_called()
                self.assertIn("CONFIRMATION_PENDING", reply)
                self.press(True, wait_for=lambda: run.called)
                run.assert_called_once()
        finally:
            os.unlink(path)

    def test_build_writes_but_does_not_execute(self):
        from actions import code_helper
        path = Path(tempfile.gettempdir()) / "alpha_build_test.py"
        with mock.patch.object(code_helper, "_write", return_value=("print(1)", path)), \
             mock.patch.object(code_helper, "_run_file", return_value="ok") as run:
            reply = code_helper._build("x", "python", "", [], 5)
            run.assert_not_called()
            self.assertIn("CONFIRMATION_PENDING", reply)
            confirm.resolve(False)
            time.sleep(0.05)
            run.assert_not_called()

    def test_dev_agent_neither_installs_nor_runs_until_confirmed(self):
        from actions import dev_agent
        plan = {"project_name": "t", "files": [{"path": "a.py"}], "entry_point": "a.py",
                "run_command": "python a.py", "dependencies": ["requests"]}
        with mock.patch.object(dev_agent, "_plan_project", return_value=plan), \
             mock.patch.object(dev_agent, "_write_file", return_value="code"), \
             mock.patch.object(dev_agent, "_open_vscode"), \
             mock.patch.object(dev_agent, "PROJECTS_DIR", Path(tempfile.mkdtemp())), \
             mock.patch.object(dev_agent, "_install_dependencies", return_value="") as inst, \
             mock.patch.object(dev_agent, "_run_project", return_value="ok") as run:
            reply = dev_agent._build_project("x", "python", "", 5)
            inst.assert_not_called()
            run.assert_not_called()
            self.assertIn("CONFIRMATION_PENDING", reply)
            self.assertIn("python a.py", self.shown[0][1])
            self.assertIn("requests", self.shown[0][1])
            self.press(True, wait_for=lambda: run.called)
            inst.assert_called_once()
            run.assert_called_once()


class FocusWindow(unittest.TestCase):
    def test_title_is_never_part_of_the_script(self):
        from actions import computer_control as cc
        hostile = '$(Remove-Item x)"; calc; "'
        result = mock.Mock(stdout="True", returncode=0)
        with mock.patch.object(cc, "_get_os", return_value="windows"), \
             mock.patch.object(cc.subprocess, "run", return_value=result) as run, \
             mock.patch.object(cc.time, "sleep"):
            cc._focus_window(hostile)
        args, kw = run.call_args
        self.assertNotIn("Remove-Item", " ".join(args[0]))
        self.assertEqual(kw["env"]["ALPHA_FOCUS_TITLE"], hostile)

    def test_a_missing_window_is_not_reported_as_success(self):
        from actions import computer_control as cc
        with mock.patch.object(cc, "_get_os", return_value="windows"), \
             mock.patch.object(cc.subprocess, "run",
                               return_value=mock.Mock(stdout="False", returncode=0)):
            self.assertIn("failed", cc._focus_window("nothing"))

    def test_mac_title_cannot_close_the_applescript_string(self):
        from actions import computer_control as cc
        with mock.patch.object(cc, "_get_os", return_value="mac"), \
             mock.patch.object(cc.subprocess, "run",
                               return_value=mock.Mock(returncode=0)) as run, \
             mock.patch.object(cc.time, "sleep"):
            cc._focus_window('x" to quit')
        self.assertIn('contains "x\\" to quit"', run.call_args[0][0][2])

    @unittest.skipUnless(platform.system() == "Windows", "needs real PowerShell")
    def test_real_powershell_does_not_execute_a_hostile_title(self):
        from actions import computer_control as cc
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "pwned.txt"
            title = f"$(Set-Content -LiteralPath '{marker}' -Value x)"
            with mock.patch.object(cc, "_get_os", return_value="windows"):
                reply = cc._focus_window(title)
            self.assertFalse(marker.exists(), "the title was executed as code")
            self.assertIn("failed", reply)


class Undo(unittest.TestCase):
    def setUp(self):
        undo.clear()

    def test_reverses_most_recent_first(self):
        order = []
        undo.push_undo("a", lambda: order.append("a"))
        undo.push_undo("b", lambda: order.append("b"))
        self.assertEqual(undo.history(), ["b", "a"])
        undo.undo_last()
        undo.undo_last()
        self.assertEqual(order, ["b", "a"])

    def test_empty_stack_says_so(self):
        self.assertIn("nothing to undo", undo.undo_last())

    def test_failed_undo_is_reported_and_not_retried(self):
        def boom():
            raise OSError("gone")
        undo.push_undo("moved file", boom)
        self.assertIn("Could not undo", undo.undo_last())
        self.assertFalse(undo.can_undo())

    def test_depth_is_bounded(self):
        for i in range(undo.MAX_DEPTH + 5):
            undo.push_undo(str(i), lambda: "")
        self.assertEqual(len(undo.history()), undo.MAX_DEPTH)


class StateTheme(unittest.TestCase):
    def test_every_spec_state_has_a_colour(self):
        for key in ("IDLE", "LISTENING", "THINKING", "SPEAKING", "VISION",
                    "PROCESSING", "WARNING", "ERROR"):
            self.assertIn(key, state_theme.DEFAULTS)

    def test_alert_outranks_everything(self):
        self.assertEqual(state_theme.key_for("LISTENING", True, True, "ERROR"), "ERROR")
        self.assertEqual(state_theme.key_for("THINKING", alert="warning"), "WARNING")

    def test_state_priority(self):
        self.assertEqual(state_theme.key_for("THINKING", speaking=True), "SPEAKING")
        self.assertEqual(state_theme.key_for("SPEAKING", muted=True), "MUTED")
        self.assertEqual(state_theme.key_for("SLEEPING"), "IDLE")
        self.assertEqual(state_theme.key_for("INITIALISING"), "IDLE")

    def test_overrides_apply_and_bad_values_are_ignored(self):
        p = state_theme.palette({"thinking": "#ABCDEF", "ERROR": "red", "NOPE": "#000000"})
        self.assertEqual(p["THINKING"], "#abcdef")
        self.assertEqual(p["ERROR"], state_theme.DEFAULTS["ERROR"])
        self.assertNotIn("NOPE", p)
        self.assertEqual(state_theme.palette("junk"), state_theme.DEFAULTS)

    def test_transition_settles_without_jumping(self):
        cur, tgt = (0.0, 0.0, 0.0), (255.0, 0.0, 0.0)
        cur = state_theme.approach(cur, tgt)
        self.assertLess(cur[0], 60)
        for _ in range(60):
            cur = state_theme.approach(cur, tgt)
        self.assertGreater(cur[0], 254)


class Identity(unittest.TestCase):
    def test_wake_phrase_names_what_the_detector_really_hears(self):
        from core import wake_word
        self.assertEqual(identity.WAKE_PHRASE.lower().replace(" ", "_"),
                         wake_word.WAKE_MODEL)

    def test_no_old_branding_in_user_facing_strings(self):
        allowed = ("Hey Jarvis", "Uploads", "_AES_SALT", "JARVIS-DASHBOARD",
                   "Dashboard Port", "Dashboard Python", "JarvisProjects")
        for name in ("main.py", "ui.py", "setup.py", "dashboard/static/app.html",
                     "dashboard/static/login.html"):
            for n, line in enumerate((ROOT / name).read_text(encoding="utf-8").splitlines(), 1):
                if "JARVIS" in line.replace("JARVIS_AI", "") or "J.A.R.V.I.S" in line:
                    if not any(a in line for a in allowed):
                        self.fail(f"{name}:{n}: {line.strip()[:90]}")

    @unittest.skipUnless(platform.system() == "Windows", "creates a real .lnk")
    def test_shortcut_button_creates_a_real_shortcut(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        import pythoncom
        import ui
        pythoncom.CoInitialize()    # the real window's thread already has COM
        win = ui.MainWindow.__new__(ui.MainWindow)
        win._log = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(ui.MainWindow, "_get_desktop_dir",
                               staticmethod(lambda: Path(tmp))):
            ui.MainWindow._create_desktop_shortcut(win)
            self.assertTrue((Path(tmp) / f"{identity.NAME}.lnk").exists(),
                            win._log.append_log.call_args)

    def test_readiness_check_never_imports_the_wake_library(self):
        # Importing it here races the detector's own import at startup.
        import subprocess
        code = ("import sys; sys.path.insert(0, '.'); from core import wake_word; "
                "wake_word.is_ready(); print('openwakeword' in sys.modules)")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, text=True,
                             capture_output=True, timeout=30)
        self.assertEqual(out.stdout.strip(), "False", out.stderr)

    def test_gitignore_has_no_inline_comments(self):
        # Git reads them as part of the pattern, which stops the pattern matching.
        for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.startswith("#"):
                self.assertNotIn(" ", line.strip(), line)

    def test_things_that_must_not_be_renamed_were_not(self):
        server = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8")
        self.assertIn("b'JARVIS-DASHBOARD-v1'", server)
        self.assertIn('"JARVIS Uploads"', server)
        self.assertIn("shutdown_jarvis", (ROOT / "main.py").read_text(encoding="utf-8"))


class WakeIsolation(unittest.TestCase):
    """The detector lives in a child process: its failures must stay there."""

    def detector(self, code, **kw):
        from core import wake_word
        self.notes, self.heard = [], []
        d = wake_word.WakeWordDetector(lambda: self.heard.append(1), logger=lambda m: None,
                                       notify=self.notes.append, **kw)
        if code is not None:
            d._command = lambda: [sys.executable, "-c", code]
        self.addCleanup(d.stop)
        return d

    def wait(self, check, seconds=10):
        deadline = time.time() + seconds
        while not check() and time.time() < deadline:
            time.sleep(0.02)
        return check()

    def test_a_detection_reaches_the_app(self):
        d = self.detector("print('READY', flush=True); print('DETECT', flush=True); "
                          "import sys; sys.stdin.buffer.read()")
        self.assertTrue(d.start())
        self.assertTrue(self.wait(lambda: self.heard))
        self.assertTrue(d.ready)

    def test_a_crashing_worker_is_bounded_and_reported(self):
        d = self.detector("import os; os._exit(3)")
        d.start()
        self.assertTrue(self.wait(lambda: not d.running))
        self.assertFalse(d.ready)
        self.assertEqual(d._restarts, d.MAX_RESTARTS)
        self.assertIn("WAKE NOW", self.notes[-1])
        self.assertIn("0x3", self.notes[-1])

    def test_the_worker_reason_reaches_the_user(self):
        d = self.detector("print('ERROR model missing', flush=True)")
        d.start()
        self.assertTrue(self.wait(lambda: not d.running))
        self.assertIn("model missing", self.notes[-1])

    def test_a_worker_lost_while_listening_is_replaced(self):
        d = self.detector("print('READY', flush=True); import sys; sys.stdin.buffer.read()")
        d.start()
        self.assertTrue(self.wait(lambda: d.ready))
        first = d._proc
        first.kill()
        self.assertTrue(self.wait(lambda: d._proc is not first and d.ready))

    def test_frames_before_ready_are_dropped(self):
        import numpy as np
        d = self.detector("import sys; sys.stdin.buffer.read()")
        d.start()
        d.feed(np.zeros(1024, dtype=np.int16))
        self.assertTrue(d._queue.empty())

    def test_stop_ends_the_process(self):
        d = self.detector("print('READY', flush=True); import sys; sys.stdin.buffer.read()")
        d.start()
        self.assertTrue(self.wait(lambda: d.ready))
        proc = d._proc
        d.stop()
        self.assertTrue(self.wait(lambda: proc.poll() is not None))
        self.assertEqual(self.notes, [])

    def test_real_model_loads_and_ignores_silence(self):
        import numpy as np
        from core import wake_word
        if not wake_word.is_ready():
            self.skipTest("wake model not installed")
        d = self.detector(None)
        d.start()
        self.assertTrue(self.wait(lambda: d.ready or not d.running, 60), self.notes)
        self.assertTrue(d.ready, self.notes)
        for _ in range(40):
            d.feed(np.zeros(1024, dtype=np.int16))
            time.sleep(0.01)
        time.sleep(0.5)
        self.assertEqual(self.heard, [])
        self.assertTrue(d.running)


class AudioRace(unittest.TestCase):
    """PortAudio must only ever be entered by one thread at a time."""

    def setUp(self):
        from core import audio_devices
        self.ad = audio_devices
        self._saved = audio_devices._cache
        audio_devices._cache = None
        self.addCleanup(setattr, audio_devices, "_cache", self._saved)

    def test_concurrent_callers_share_one_scan(self):
        import threading
        calls = []

        def slow_query():
            calls.append(1)
            time.sleep(0.15)
            return {"input": ["mic"], "output": ["spk"]}

        results = []
        with mock.patch.object(self.ad, "_query", slow_query):
            threads = [threading.Thread(target=lambda: results.append(self.ad.list_devices("input")))
                       for _ in range(4)]
            threads.append(threading.Thread(target=self.ad._scan))
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(len(calls), 1)
        self.assertEqual(results, [["mic"]] * 4)

    def test_refresh_really_rescans(self):
        calls = []

        def query():
            calls.append(1)
            return {"input": [], "output": []}

        with mock.patch.object(self.ad, "_query", query):
            self.ad.list_devices("input")
            self.ad.list_devices("input")
            self.ad.list_devices("input", refresh=True)
        self.assertEqual(len(calls), 2)

    def test_resolve_and_a_running_scan_never_overlap(self):
        import threading
        inside, worst = [0], [0]

        def enter():
            inside[0] += 1
            worst[0] = max(worst[0], inside[0])
            time.sleep(0.02)
            inside[0] -= 1

        def fake_query():
            enter()
            return {"input": [], "output": []}

        def fake_resolve(name, kind):
            enter()

        with mock.patch.object(self.ad, "_query", fake_query), \
             mock.patch.object(self.ad, "_resolve", fake_resolve):
            threads = [threading.Thread(target=self.ad._scan)]
            threads += [threading.Thread(target=self.ad.resolve, args=("m", "input"))
                        for _ in range(4)]
            threads += [threading.Thread(target=self.ad.locked, args=(enter,)) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        self.assertEqual(worst[0], 1)

    def test_close_stream_closes_even_when_stop_fails(self):
        stream = mock.Mock()
        stream.stop.side_effect = RuntimeError("device gone")
        with self.assertRaises(RuntimeError):
            self.ad.close_stream(stream)
        stream.close.assert_called_once()

    def test_session_does_not_block_the_event_loop_on_devices(self):
        source = (ROOT / "main.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("await asyncio.to_thread(audio_devices.resolve"), 2)
        self.assertNotIn("= _open_mic(", source)
        self.assertNotIn("= _open_spk(", source)
        self.assertNotIn("stream.stop()", source)


class Memory(unittest.TestCase):
    def test_survives_reload_is_searchable_and_forgettable(self):
        from memory import memory_manager as mm
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "long_term.json"
            with mock.patch.object(mm, "MEMORY_PATH", path):
                mm.update_memory({"preferences": {"favorite_food": {"value": "biryani"}}})
                self.assertTrue(path.exists())
                on_disk = json.loads(path.read_text(encoding="utf-8"))
                self.assertIn("favorite_food", on_disk["preferences"])
                self.assertIn("biryani", mm.search_memory("biryani"))
                mm.forget("favorite_food", "preferences")
                self.assertNotIn("favorite_food",
                                 mm.load_memory().get("preferences", {}))


class Plugins(unittest.TestCase):
    def _discover(self, source):
        from core.plugin_loader import discover_plugins
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "sample.py").write_text(source, encoding="utf-8")
            return discover_plugins(plugins_dir=Path(tmp), core_tool_names=set(),
                                    logger=lambda m: None, notify=lambda m: None)

    def test_valid_plugin_loads_and_runs(self):
        reg = self._discover(
            'PLUGIN = {"name": "sample_tool", "description": "d",\n'
            '          "parameters": {"type": "OBJECT", "properties": {}}}\n'
            'def run(parameters, player=None, session_memory=None):\n'
            '    return "hello"\n')
        self.assertTrue(reg.has("sample_tool"))
        self.assertEqual(reg.run("sample_tool", {}), "hello")

    def test_a_broken_plugin_does_not_take_the_loader_down(self):
        reg = self._discover("raise RuntimeError('broken at import')\n")
        self.assertFalse(reg.has("sample_tool"))


class Platform(unittest.TestCase):
    def test_wayland_is_not_treated_as_x11(self):
        from core import platform_support as ps
        with mock.patch.object(ps.platform, "system", return_value="Linux"), \
             mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}):
            s = ps.detect()
        self.assertFalse(s.can_control_input)
        self.assertFalse(s.can_capture_screen)
        self.assertTrue(ps.describe_for_model(s))

    def test_no_display_means_no_control(self):
        from core import platform_support as ps
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_SESSION_TYPE", "WAYLAND_DISPLAY", "DISPLAY")}
        with mock.patch.object(ps.platform, "system", return_value="Linux"), \
             mock.patch.dict(os.environ, env, clear=True):
            self.assertFalse(ps.detect().fully_capable)


class Hud(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        import ui
        cls.ui = ui

    def canvas(self, state="LISTENING"):
        hud = self.ui.HudCanvas("face.png", identity.NAME)
        hud._tmr.stop()
        hud.resize(500, 500)
        hud.state = state
        hud.hud_style = "face"
        hud.low_resource = False    # never inherit the owner's real setting
        return hud

    def frames_per_600_ticks(self, hud):
        hud._on_screen = lambda: True
        count = []
        hud.update = lambda: count.append(1)
        for _ in range(600):
            hud._step()
        return len(count)

    def test_idle_paints_far_less_than_talking(self):
        idle = self.frames_per_600_ticks(self.canvas("LISTENING"))
        busy = self.frames_per_600_ticks(self.canvas("THINKING"))
        self.assertLessEqual(idle, 135)     # 12 Hz plus forced blink frames
        self.assertGreaterEqual(busy, 300)  # 30 Hz, what lip sync needs

    def test_low_resource_halves_the_rate(self):
        normal = self.frames_per_600_ticks(self.canvas())
        hud = self.canvas()
        hud.low_resource = True
        self.assertLess(self.frames_per_600_ticks(hud), normal * 0.65)

    def test_every_state_and_style_paints_without_error(self):
        from PyQt6.QtGui import QImage
        img = QImage(500, 500, QImage.Format.Format_ARGB32_Premultiplied)
        for style in ("face", "core", "particle", "astra"):
            for state in ("LISTENING", "THINKING", "VISION", "SLEEPING"):
                for alert in ("", "WARNING", "ERROR"):
                    hud = self.canvas(state)
                    hud.hud_style = style
                    hud.alert = alert
                    hud._step()
                    hud.render(img)

    def test_colour_follows_state_and_alert(self):
        hud = self.canvas("THINKING")
        for _ in range(120):
            hud._step()
        self.assertEqual(hud._tint_q().name(), state_theme.DEFAULTS["THINKING"])
        hud.alert = "ERROR"
        for _ in range(120):
            hud._step()
        self.assertEqual(hud._tint_q().name(), state_theme.DEFAULTS["ERROR"])


if __name__ == "__main__":
    unittest.main()
