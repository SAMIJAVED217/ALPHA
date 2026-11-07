"""Measures the computer for the mentor's setup advice: Windows, hardware, WSL, Ubuntu, ROS 2 and Gazebo.

Every command here is fixed text and only reads. Nothing the model writes is ever run. The
report holds no names, paths or accounts, only what matters for installing robotics software.
"""

from __future__ import annotations

import logging
import platform
import re
import subprocess
import sys
from typing import Callable

log = logging.getLogger(__name__)

_NO_WINDOW = 0x08000000
TIMEOUT_SECONDS = 40
GPU_COMMAND = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
               "(Get-CimInstance Win32_VideoController).Name"]
WSL_LIST = ["wsl.exe", "--list", "--verbose"]
WSL_STATUS = ["wsl.exe", "--status"]
# Runs inside the default Linux, and only reads. Starting WSL can take a few seconds.
INSIDE_LINUX = ["wsl.exe", "--exec", "sh", "-c",
                "grep -E '^(PRETTY_NAME|VERSION_ID)=' /etc/os-release; "
                "echo ROS: $(ls /opt/ros 2>/dev/null); "
                "echo GAZEBO: $(command -v gz gazebo 2>/dev/null); "
                "echo MEMORY: $(grep MemTotal /proc/meminfo)"]


def run_command(command: list[str]) -> str:
    """Returns the output, or an empty string if the command is missing, fails or takes too long."""
    try:
        finished = subprocess.run(command, capture_output=True, timeout=TIMEOUT_SECONDS, creationflags=_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as error:
        log.info("%s did not run: %s", command[0], type(error).__name__)
        return ""
    return _text_of(finished.stdout).strip() if finished.returncode == 0 else ""


def _text_of(raw: bytes) -> str:
    """wsl.exe writes UTF-16 for its own messages, and UTF-8 for what Linux prints."""
    if raw[1:2] == b"\x00" or raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff").replace("\x00", "")
    return raw.decode("utf-8", errors="replace")


def _cpu_name() -> str:
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
            return " ".join(str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).split())
    except OSError:
        return platform.processor() or "unknown"


def _windows_name() -> str:
    """Windows 11 still calls itself 10 in many places, so the build number decides."""
    build = sys.getwindowsversion().build if sys.platform == "win32" else 0
    family = "Windows 11" if build >= 22000 else "Windows 10" if build else platform.system()
    edition = platform.win32_edition() if sys.platform == "win32" else ""
    return f"{family} {edition or ''} (build {build})".replace("  ", " ")


def measure(run: Callable[[list[str]], str] = run_command) -> str:
    import psutil

    memory = psutil.virtual_memory()
    lines = [
        f"- Operating system: {_windows_name()}",
        f"- Processor: {_cpu_name()}, {psutil.cpu_count(logical=False)} cores, {psutil.cpu_count()} threads",
        f"- Memory: {memory.total / 2**30:.1f} GB in total, {memory.available / 2**30:.1f} GB free right now",
        f"- Graphics: {', '.join(run(GPU_COMMAND).splitlines()) or 'unknown'}",
    ]
    for drive in ("C:\\", "D:\\"):
        try:
            free = psutil.disk_usage(drive).free / 2**30
            lines.append(f"- Free space on drive {drive[0]}: {free:.0f} GB")
        except OSError:
            pass
    distros = run(WSL_LIST)
    if not distros:
        lines.append("- WSL: not installed, or no Linux distribution is set up")
        return "\n".join(lines)
    lines.append("- WSL distributions:\n" + "\n".join(f"    {line.strip()}" for line in distros.splitlines() if line.strip()))
    status = run(WSL_STATUS)
    if status:
        default = re.search(r"Default Version:\s*(\d)", status)
        if default:
            lines.append(f"- WSL default version: {default[1]}")
    inside = run(INSIDE_LINUX)
    if inside:
        lines.append("- Inside the default Linux:\n" + "\n".join(f"    {line.strip()}" for line in inside.splitlines() if line.strip()))
    else:
        lines.append("- The default Linux could not be started to look inside it")
    return "\n".join(lines)
