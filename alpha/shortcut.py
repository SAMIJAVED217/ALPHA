"""Creates the desktop shortcut. Run with: python -m alpha.shortcut"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from . import paths

# Paths reach PowerShell through environment variables, never inside the command text.
_SCRIPT = """
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
$path = Join-Path $desktop 'ALPHA.lnk'
# A shortcut to something else is left alone, and this one goes beside it.
if (Test-Path $path) {
    if ($shell.CreateShortcut($path).TargetPath -ne $env:ALPHA_LINK_TARGET) {
        $path = Join-Path $desktop 'ALPHA (new).lnk'
    }
}
$link = $shell.CreateShortcut($path)
$link.TargetPath = $env:ALPHA_LINK_TARGET
$link.Arguments = '-m alpha'
$link.WorkingDirectory = $env:ALPHA_LINK_FOLDER
$link.IconLocation = $env:ALPHA_LINK_ICON
$link.Description = 'ALPHA, your personal AI companion'
$link.Save()
Write-Output $link.FullName
"""


def windowless_python() -> Path:
    return Path(sys.executable).with_name("pythonw.exe")


def create() -> str:
    project = Path(__file__).resolve().parent.parent
    environment = dict(
        os.environ,
        ALPHA_LINK_TARGET=str(windowless_python()),
        ALPHA_LINK_FOLDER=str(project),
        ALPHA_LINK_ICON=str(paths.assets_dir() / "alpha.ico"),
    )
    finished = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    return finished.stdout.strip()


if __name__ == "__main__":
    print(f"Created {create()}")
