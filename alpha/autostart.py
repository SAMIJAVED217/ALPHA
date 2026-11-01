"""Starting APLHA with Windows, through a shortcut in the owner's own Startup folder."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import paths
from .shortcut import windowless_python

_NO_WINDOW = 0x08000000
# Paths reach PowerShell through environment variables, never inside the command text.
_SCRIPT = """
$link = (New-Object -ComObject WScript.Shell).CreateShortcut($env:ALPHA_LINK_PATH)
$link.TargetPath = $env:ALPHA_LINK_TARGET
$link.Arguments = '-m alpha'
$link.WorkingDirectory = $env:ALPHA_LINK_FOLDER
$link.IconLocation = $env:ALPHA_LINK_ICON
$link.Save()
"""


def startup_link() -> Path:
    return Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "APLHA.lnk"


def is_on() -> bool:
    return startup_link().exists()


def turn_on() -> None:
    environment = dict(
        os.environ,
        ALPHA_LINK_PATH=str(startup_link()),
        ALPHA_LINK_TARGET=str(windowless_python()),
        ALPHA_LINK_FOLDER=str(Path(__file__).resolve().parent.parent),
        ALPHA_LINK_ICON=str(paths.assets_dir() / "alpha.ico"),
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
        env=environment, check=True, capture_output=True, timeout=20, creationflags=_NO_WINDOW,
    )


def turn_off() -> None:
    startup_link().unlink(missing_ok=True)
