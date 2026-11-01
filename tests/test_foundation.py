import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from alpha import keystore, paths, readiness, settings
from alpha.state import DEFAULT_COLOURS, State

ROOT = Path(__file__).resolve().parent.parent


def test_isolation_is_really_in_force():
    assert paths.data_dir() != paths.real_data_dir()
    assert Path(os.environ["ALPHA_HOME"]) == paths.data_dir()
    assert type(__import__("keyring").get_keyring()).__name__ == "MemoryKeyring"
    with pytest.raises(ImportError):
        import sounddevice  # noqa: F401


def test_data_dir_refuses_the_real_folder_during_tests(monkeypatch):
    monkeypatch.delenv(paths.HOME_VARIABLE)
    with pytest.raises(RuntimeError):
        paths.data_dir()


def test_first_run_has_defaults_and_no_file():
    assert not paths.settings_file().exists()
    loaded = settings.load()
    assert loaded.setup_complete is False
    assert loaded.colours == DEFAULT_COLOURS


def test_settings_persist():
    chosen = settings.load()
    chosen.setup_complete = True
    chosen.microphone = "Desk mic"
    chosen.speakers = "Headphones"
    chosen.talk_key = "F8"
    chosen.colours[State.THINKING.value] = "#123456"
    settings.save(chosen)
    again = settings.load()
    assert again == chosen


@pytest.mark.parametrize("content", ["not json", "[1,2]", '{"talk_key": "ENTER", "colours": {"idle": "blue"}}'])
def test_damaged_settings_fall_back_safely(content):
    paths.settings_file().write_text(content, encoding="utf-8")
    loaded = settings.load()
    assert loaded.talk_key == settings.DEFAULT_VOICE_KEY
    assert loaded.colours == DEFAULT_COLOURS


def test_key_goes_to_the_credential_store_and_never_into_settings():
    keystore.save_key("  secret-value  ")
    assert keystore.load_key() == "secret-value"
    settings.save(settings.load())
    assert "secret-value" not in paths.settings_file().read_text(encoding="utf-8")
    assert not any("secret-value" in path.read_text(errors="ignore") for path in paths.data_dir().glob("*.json"))
    keystore.delete_key()
    assert not keystore.has_key()


def test_empty_key_is_refused():
    with pytest.raises(keystore.KeyStoreError):
        keystore.save_key("   ")


def test_readiness_uses_files_not_imports():
    script = (
        "import sys\n"
        "from alpha import readiness\n"
        "readiness.missing()\n"
        "print('google.genai' in sys.modules, 'sounddevice' in sys.modules, 'PySide6.QtCore' in sys.modules)\n"
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=ROOT, check=True)
    assert done.stdout.split() == ["False", "False", "False"]
    assert readiness.is_installed("keyring")
    assert not readiness.is_installed("no-such-package-here")


def test_gitignore_comments_are_on_their_own_lines():
    for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines():
        assert "#" not in line or line.startswith("#")


def test_product_never_uses_the_forbidden_names():
    forbidden = ("jarvis", "astra")
    sources = list((ROOT / "alpha").rglob("*.py"))
    assert sources
    for source in sources:
        text = source.read_text(encoding="utf-8").lower()
        assert not any(name in text for name in forbidden), source


def test_no_gpl_only_binding_is_listed():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "pyqt" not in requirements
    assert "pyside6" in requirements
    dependencies = (ROOT / "docs" / "DEPENDENCIES.md").read_text(encoding="utf-8").lower()
    for line in requirements.splitlines():
        assert line.split("==")[0] in dependencies


def test_settings_file_holds_only_known_fields():
    settings.save(settings.load())
    saved = json.loads(paths.settings_file().read_text(encoding="utf-8"))
    assert set(saved) == {"setup_complete", "model", "microphone", "speakers", "talk_key", "speech_engine", "speech_language", "character", "hands_free_at_start", "morning_briefing", "skip_ads", "last_briefing", "colours"}
