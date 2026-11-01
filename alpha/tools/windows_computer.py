"""The real Windows calls behind the computer tools.

Every method checks the result afterwards and raises `ToolRefused` when Windows
did not do what was asked. Nothing here builds a command out of supplied text.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from urllib.parse import quote_plus

import httpx

from .registry import ToolRefused

_NO_WINDOW = 0x08000000
_CF_UNICODETEXT = 13
_GMEM_MOVEABLE = 0x0002
# Private copies, so these pointer-sized settings cannot disturb other users of windll.
_USER32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
if _USER32 is not None:
    _USER32.OpenClipboard.argtypes = [wintypes.HWND]
    _USER32.GetClipboardData.restype = wintypes.HANDLE
    _USER32.GetClipboardData.argtypes = [wintypes.UINT]
    _USER32.SetClipboardData.restype = wintypes.HANDLE
    _USER32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    _KERNEL32.GlobalLock.restype = wintypes.LPVOID
    _KERNEL32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    _KERNEL32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    _KERNEL32.GlobalAlloc.restype = wintypes.HGLOBAL
    _KERNEL32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_FO_DELETE = 3
# FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
_RECYCLE_QUIETLY = 0x0040 | 0x0010 | 0x0004 | 0x0400


class _FileOperation(ctypes.Structure):
    _fields_ = [
        ("window", wintypes.HWND),
        ("operation", wintypes.UINT),
        ("source", wintypes.LPCWSTR),
        ("target", wintypes.LPCWSTR),
        ("flags", ctypes.c_ushort),
        ("aborted", wintypes.BOOL),
        ("mappings", wintypes.LPVOID),
        ("title", wintypes.LPCWSTR),
    ]


def _open_clipboard() -> None:
    """Another program may hold the clipboard for a moment, so it is tried a few times."""
    for _ in range(10):
        if _USER32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise ToolRefused("Another program is holding the clipboard.")


_VIDEO_ID = re.compile(r'"videoId":"([A-Za-z0-9_-]{11})"')
_MEDIA_KEYS = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
# Apps can take a long time to switch on accessibility the first time they are read.
_READ_WINDOW_SECONDS = 60
_SW_MINIMIZE, _SW_MAXIMIZE, _SW_RESTORE = 6, 3, 9
_WM_CLOSE = 0x0010
_INPUT_MOUSE, _INPUT_KEYBOARD = 0, 1
_KEYUP, _UNICODE = 0x0002, 0x0004
_LEFT_DOWN, _LEFT_UP, _RIGHT_DOWN, _RIGHT_UP = 0x0002, 0x0004, 0x0008, 0x0010

_READ_BRIGHTNESS = "(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness"
_SET_BRIGHTNESS = (
    "Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods | "
    "Invoke-CimMethod -MethodName WmiSetBrightness -Arguments @{Timeout=1; Brightness=[int]$env:ALPHA_BRIGHTNESS}"
)

_LIST_APPS = (
    "[Console]::OutputEncoding = [Text.Encoding]::UTF8; "
    "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"
)
_NOT_REALLY_APPS = re.compile(r"uninstall|setup|readme|manual|\bhelp\b", re.IGNORECASE)

KEY_CODES = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "home": 0x24, "end": 0x23, "pageup": 0x21,
    "pagedown": 0x22, "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    **{f"f{number}": 0x6F + number for number in range(1, 13)},
    **{letter: ord(letter.upper()) for letter in "abcdefghijklmnopqrstuvwxyz0123456789"},
}


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_void_p),
    ]


class _Union(ctypes.Union):
    _fields_ = [("ki", _KeyboardInput), ("mi", _MouseInput)]


class _Input(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _Union)]


def _key_event(code: int, scan: int, flags: int) -> _Input:
    event = _Input(type=_INPUT_KEYBOARD)
    event.ki = _KeyboardInput(code, scan, flags, 0, None)
    return event


def _mouse_event(flags: int) -> _Input:
    event = _Input(type=_INPUT_MOUSE)
    event.mi = _MouseInput(0, 0, 0, flags, 0, None)
    return event


class WindowsComputer:
    def __init__(self):
        self._user32 = ctypes.windll.user32
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._volume_process: subprocess.Popen | None = None
        self._apps: dict[str, str] | None = None

    # Volume, through a helper process that is restarted if it ever dies.

    def _ask_volume(self, request: dict) -> tuple[int, bool]:
        for _ in range(2):
            if self._volume_process is None or self._volume_process.poll() is not None:
                self._volume_process = subprocess.Popen(
                    [sys.executable, "-m", "alpha.tools.volume_worker"],
                    cwd=str(Path(__file__).resolve().parent.parent.parent),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    creationflags=_NO_WINDOW,
                )
            try:
                self._volume_process.stdin.write(json.dumps(request) + "\n")
                self._volume_process.stdin.flush()
                reply = self._volume_process.stdout.readline()
            except OSError:
                reply = ""
            if reply:
                answer = json.loads(reply)
                return answer["percent"], answer["muted"]
            self._volume_process = None
        raise ToolRefused("The volume helper stopped working.")

    def volume(self) -> tuple[int, bool]:
        return self._ask_volume({})

    def set_volume(self, percent: int) -> None:
        self._ask_volume({"percent": percent})

    def set_mute(self, muted: bool) -> None:
        self._ask_volume({"muted": muted})

    # Brightness. Only built-in screens answer this. The number travels by environment variable.

    def _powershell(self, script: str, extra: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, **(extra or {})},
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            creationflags=_NO_WINDOW,
        )

    def brightness(self) -> int:
        finished = self._powershell(_READ_BRIGHTNESS)
        found = finished.stdout.split()
        if finished.returncode != 0 or not found or not found[0].isdigit():
            raise ToolRefused("This screen does not let Windows change its brightness.")
        return int(found[0])

    def set_brightness(self, percent: int) -> None:
        finished = self._powershell(_SET_BRIGHTNESS, {"ALPHA_BRIGHTNESS": str(percent)})
        if finished.returncode != 0:
            raise ToolRefused("Windows refused to change the brightness.")

    # Apps. Only names in Windows' own app list can be opened, including Store apps.

    def apps(self) -> dict[str, str]:
        if self._apps is None:
            finished = self._powershell(_LIST_APPS)
            listed = json.loads(finished.stdout or "[]")
            listed = listed if isinstance(listed, list) else [listed]
            self._apps = {
                item["Name"]: item["AppID"]
                for item in listed
                if item.get("AppID") and not _NOT_REALLY_APPS.search(item["Name"])
            }
        return self._apps

    def open_shortcut(self, app_id: str) -> None:
        os.startfile(f"shell:AppsFolder\\{app_id}")

    # Windows.

    def windows(self) -> dict[int, str]:
        found: dict[int, str] = {}

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def visit(handle, _):
            if self._user32.IsWindowVisible(handle):
                length = self._user32.GetWindowTextLengthW(handle)
                if length:
                    title = ctypes.create_unicode_buffer(length + 1)
                    self._user32.GetWindowTextW(handle, title, length + 1)
                    found[int(handle)] = title.value
            return True

        self._user32.EnumWindows(visit, 0)
        return found

    def read_window(self, handle: int) -> list[dict]:
        finished = subprocess.run(
            [sys.executable, "-m", "alpha.tools.uia_worker", str(handle)],
            cwd=str(Path(__file__).resolve().parent.parent.parent),
            capture_output=True,
            timeout=_READ_WINDOW_SECONDS,
            creationflags=_NO_WINDOW,
        )
        if finished.returncode != 0:
            raise ToolRefused("That window could not be read.")
        return json.loads(finished.stdout.decode("utf-8"))

    def clipboard_text(self) -> str:
        _open_clipboard()
        try:
            handle = _USER32.GetClipboardData(_CF_UNICODETEXT)
            if not handle:
                return ""
            start = _KERNEL32.GlobalLock(handle)
            try:
                return ctypes.wstring_at(start)
            finally:
                _KERNEL32.GlobalUnlock(handle)
        finally:
            _USER32.CloseClipboard()

    def set_clipboard(self, text: str) -> None:
        data = text.encode("utf-16-le") + b"\0\0"
        handle = _KERNEL32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
        start = _KERNEL32.GlobalLock(handle)
        ctypes.memmove(start, data, len(data))
        _KERNEL32.GlobalUnlock(handle)
        _open_clipboard()
        try:
            _USER32.EmptyClipboard()
            if not _USER32.SetClipboardData(_CF_UNICODETEXT, handle):
                raise ToolRefused("Windows would not change the clipboard.")
        finally:
            _USER32.CloseClipboard()

    def recycle(self, path: Path) -> None:
        request = _FileOperation(None, _FO_DELETE, str(path) + "\0", None, _RECYCLE_QUIETLY, False, None, None)
        if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(request)) != 0:
            raise ToolRefused(f"Windows would not recycle {path.name}.")

    def first_video(self, search: str) -> str | None:
        """The top YouTube result, so playback can start without anyone clicking it."""
        address = f"https://www.youtube.com/results?search_query={quote_plus(search)}"
        headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en"}
        try:
            page = httpx.get(address, headers=headers, timeout=15, follow_redirects=True).text
        except httpx.HTTPError:
            return None
        found = _VIDEO_ID.search(page)
        return found.group(1) if found else None

    def media_key(self, action: str) -> None:
        code = _MEDIA_KEYS[action]
        self._send([_key_event(code, 0, 0), _key_event(code, 0, _KEYUP)])

    def power(self, action: str) -> None:
        arguments = {"shutdown": ("/s", "/t", "30"), "restart": ("/r", "/t", "30"), "cancel": ("/a",)}[action]
        subprocess.run(["shutdown.exe", *arguments], check=True, timeout=10, creationflags=_NO_WINDOW)

    def lock(self) -> None:
        if not ctypes.windll.user32.LockWorkStation():
            raise ToolRefused("Windows would not lock.")

    def sleep(self) -> None:
        subprocess.run(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"], check=True, timeout=10, creationflags=_NO_WINDOW
        )

    def open_url(self, url: str) -> None:
        os.startfile(url)

    def show_window(self, handle: int, how: str) -> None:
        command = {"minimize": _SW_MINIMIZE, "maximize": _SW_MAXIMIZE, "restore": _SW_RESTORE}[how]
        self._user32.ShowWindow(handle, command)

    def focus_window(self, handle: int) -> bool:
        if self._user32.IsIconic(handle):
            self._user32.ShowWindow(handle, _SW_RESTORE)
        # Windows only lets the active app hand over focus, and a key press counts as activity.
        self._send([_key_event(0x12, 0, 0), _key_event(0x12, 0, _KEYUP)])
        self._user32.SetForegroundWindow(handle)
        time.sleep(0.2)
        return self.in_front(handle)

    def in_front(self, handle: int) -> bool:
        return int(self._user32.GetForegroundWindow() or 0) == handle

    def close_window(self, handle: int) -> bool:
        self._user32.PostMessageW(handle, _WM_CLOSE, 0, 0)
        time.sleep(1.0)
        return not self._user32.IsWindow(handle)

    # Keyboard and mouse.

    def _send(self, events: list[_Input]) -> None:
        batch = (_Input * len(events))(*events)
        sent = self._user32.SendInput(len(events), batch, ctypes.sizeof(_Input))
        if sent != len(events):
            raise ToolRefused("Windows blocked the input. Some windows, such as admin ones, refuse it.")

    def type_text(self, text: str) -> None:
        events = []
        raw = text.replace("\r\n", "\n").encode("utf-16-le")
        for index in range(0, len(raw), 2):
            unit = int.from_bytes(raw[index : index + 2], "little")
            if unit == 10:
                events += [_key_event(KEY_CODES["enter"], 0, 0), _key_event(KEY_CODES["enter"], 0, _KEYUP)]
            else:
                events += [_key_event(0, unit, _UNICODE), _key_event(0, unit, _UNICODE | _KEYUP)]
        self._send(events)

    def press_keys(self, names: list[str]) -> None:
        codes = [KEY_CODES[name] for name in names]
        events = [_key_event(code, 0, 0) for code in codes]
        events += [_key_event(code, 0, _KEYUP) for code in reversed(codes)]
        self._send(events)

    def screen_size(self) -> tuple[int, int]:
        return self._user32.GetSystemMetrics(0), self._user32.GetSystemMetrics(1)

    def click(self, x: int, y: int, right: bool) -> None:
        self._user32.SetCursorPos(x, y)
        down, up = (_RIGHT_DOWN, _RIGHT_UP) if right else (_LEFT_DOWN, _LEFT_UP)
        self._send([_mouse_event(down), _mouse_event(up)])
