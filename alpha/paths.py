"""Where ALPHA keeps its files on this machine.

Tests point ALPHA_HOME at a temporary folder before anything else runs, and
`data_dir` refuses to fall back to the real folder while they are running.
"""

from __future__ import annotations

import os
from pathlib import Path

HOME_VARIABLE = "ALPHA_HOME"
TEST_GUARD_VARIABLE = "ALPHA_UNDER_TEST"


def real_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(base) / "ALPHA"


def data_dir() -> Path:
    override = os.environ.get(HOME_VARIABLE)
    if override:
        folder = Path(override)
    elif os.environ.get(TEST_GUARD_VARIABLE):
        raise RuntimeError("Tests must set ALPHA_HOME to a temporary folder.")
    else:
        folder = real_data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def settings_file() -> Path:
    return data_dir() / "settings.json"


def log_file() -> Path:
    folder = data_dir() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "alpha.log"


def crash_file() -> Path:
    return log_file().with_name("crash.txt")


def lock_file() -> Path:
    return data_dir() / "alpha.lock"


def created_files_dir() -> Path:
    """The only folder the file-creation action may write to."""
    folder = data_dir() / "Created files"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def assets_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "assets"
