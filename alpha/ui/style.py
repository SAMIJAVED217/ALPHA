"""The look of ALPHA's windows."""

import ctypes
import sys

BACKGROUND = "#05070d"
_GWL_STYLE = -16
_WS_BORDER = 0x00800000
_WS_MAXIMIZE = 0x01000000
# SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED
_REFRESH_FRAME = 0x0001 | 0x0002 | 0x0004 | 0x0020

SHEET = """
QWidget { color: #dfe7f5; font-family: "Segoe UI"; font-size: 14px; }
QMainWindow { background: #05070d; }
QMenu { background: #0a0e18; border: 1px solid #263149; }
QMenu::item:selected { background: #1c2740; }
QDialog { background: #0a0e18; }
QDialog QWidget { background: #0a0e18; }
QFrame#hudPanel, QFrame#glass { background: rgba(8, 18, 42, 170); border: 1px solid rgba(70, 120, 220, 70); }
QFrame#hudPanel QWidget { background: transparent; }
QLabel#hudTitle { font-size: 12px; font-weight: 700; letter-spacing: 4px; color: #dfe7f5; }
QLabel#hudName { font-size: 11px; letter-spacing: 2px; color: #7c8cab; }
QLabel#hudValue { font-family: "Consolas"; font-size: 12px; color: #e8f1ff; }
QLabel#hudClock { font-family: "Consolas"; font-size: 13px; letter-spacing: 2px; color: #93a4c4; }
QLabel#wordmark { font-size: 22px; font-weight: 700; letter-spacing: 6px; color: #f2f6ff; }
QLabel#detail { color: #93a4c4; }
QLabel#heading { font-size: 18px; font-weight: 600; color: #f2f6ff; }
QLabel#keyStatus { color: #93a4c4; }
QLabel#micOff { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600;
    color: #93a4c4; background: #121826; }
QLabel#micOpening { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 600;
    color: #1a1000; background: #ffb020; }
QLabel#micLocal { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 700;
    color: #1a1000; background: #ffb020; }
QLabel#micOpen { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 700;
    color: #ffffff; background: #e0243a; }
QPlainTextEdit { background: transparent; border: none; color: #c6d2e8; }
QLineEdit, QComboBox { background: #111725; border: 1px solid #263149; border-radius: 8px;
    padding: 8px 10px; selection-background-color: #2f6bff; }
QLineEdit:focus, QComboBox:focus { border-color: #2f6bff; }
QComboBox QAbstractItemView { background: #111725; border: 1px solid #263149;
    selection-background-color: #2f6bff; }
QPushButton { background: #151c2d; border: 1px solid #2a3650; border-radius: 8px; padding: 8px 16px; }
QPushButton:hover { background: #1c2740; }
QPushButton:checked { background: #10283a; border-color: #19e3e3; color: #bff7f7; }
QPushButton#share:checked { background: #e0243a; border-color: #e0243a; color: #ffffff; font-weight: 700; }
QPushButton:disabled { color: #566380; border-color: #1c2436; }
QFrame#card { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(14, 26, 66, 248), stop:1 rgba(24, 14, 58, 248));
    border: 1px solid rgba(34, 230, 255, 190); border-top: 2px solid #22e6ff; border-radius: 16px; }
QFrame#card QWidget { background: transparent; }
QLabel#cardHeading { font-size: 11px; font-weight: 700; letter-spacing: 3px; color: #22e6ff; }
QLabel#cardTitle { font-size: 19px; font-weight: 600; color: #ffffff; }
QLabel#cardWhere { font-size: 13px; color: #b9c8ea; }
QLabel#cardCountdown { font-size: 12px; color: #93a4c4; }
QFrame#card QPlainTextEdit#cardPreview { background: rgba(6, 12, 36, 200); border: 1px solid rgba(120, 170, 255, 70);
    border-radius: 8px; color: #dfe9f7; padding: 6px; }
QFrame#card QLineEdit#cardKeyword { background: rgba(6, 12, 36, 200); border: 1px solid rgba(34, 230, 255, 170);
    border-radius: 8px; color: #ffffff; padding: 9px 10px; }
QProgressBar#cardTime { background: rgba(120, 170, 255, 40); border: none; border-radius: 1px; }
QProgressBar#cardTime::chunk { background: #22e6ff; border-radius: 1px; }
QFrame#card QPushButton#cardCancel { background: transparent; border: 1px solid rgba(120, 170, 255, 110);
    border-radius: 10px; padding: 9px 20px; color: #d3def5; font-weight: 600; }
QFrame#card QPushButton#cardCancel:hover { background: rgba(120, 170, 255, 40); border-color: #9dbbff; }
QFrame#card QPushButton#confirm { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #22e6ff, stop:1 #4d8dff);
    border: none; border-radius: 10px; padding: 9px 26px; color: #04101f; font-weight: 700; letter-spacing: 1px; }
QFrame#card QPushButton#confirm:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #6df0ff, stop:1 #7aabff); }
QFrame#card QPushButton#confirm:disabled { background: rgba(46, 70, 150, 150); color: rgba(255, 255, 255, 110); }
/* Deep navy base, with cyan for information and violet for emphasis. */
QFrame#hudPanel, QFrame#glass {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(18, 34, 84, 225), stop:1 rgba(26, 14, 66, 225));
    border: 1px solid rgba(34, 230, 255, 150); }
QLabel#hudTitle { font-size: 13px; font-weight: 700; letter-spacing: 3px; color: #22e6ff; }
QLabel#hudName { font-size: 11px; font-weight: 600; letter-spacing: 1px; color: #b9c8ea; }
QLabel#hudValue { font-family: "Consolas"; font-size: 13px; font-weight: 700; color: #ffffff; }
QLabel#hudClock { font-family: "Consolas"; font-size: 15px; font-weight: 700; color: #22e6ff; }
QLabel#wordmark { font-size: 24px; font-weight: 800; letter-spacing: 7px; color: #ffffff; }
QLabel#tagline { font-size: 12px; font-weight: 600; color: #c79bff; }
QLabel#detail { color: #d3def5; }
QPlainTextEdit { background: rgba(14, 24, 62, 215); border: 1px solid rgba(34, 230, 255, 110); color: #eaf1ff; padding: 4px; }
QLineEdit { background: rgba(14, 24, 62, 235); border: 1px solid rgba(34, 230, 255, 170); color: #ffffff; }
QPushButton { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(46, 70, 150, 235), stop:1 rgba(30, 36, 100, 235));
    border: 1px solid rgba(120, 170, 255, 170); color: #ffffff; font-weight: 600; }
QPushButton:hover { background: rgba(120, 72, 220, 235); border-color: #c79bff; }
QPushButton:checked { background: rgba(0, 150, 170, 235); border-color: #22e6ff; color: #ffffff; }
QLabel#micOff { padding: 4px 12px; border-radius: 12px; font-size: 12px; font-weight: 700;
    color: #ffffff; background: rgba(46, 70, 150, 235); }
QFrame#brandNotch { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 rgba(6, 18, 40, 120), stop:1 rgba(10, 34, 70, 200));
    border: 1px solid rgba(90, 190, 255, 140); border-top: none;
    border-bottom-left-radius: 20px; border-bottom-right-radius: 20px; }
QFrame#brandNotch QLabel { background: transparent; }
QScrollArea#characterList, QWidget#characterHolder, QScrollArea#characterList > QWidget {
    background: transparent; border: none; }
QScrollBar:vertical { background: transparent; width: 8px; }
QScrollBar::handle:vertical { background: rgba(34, 230, 255, 130); border-radius: 4px; min-height: 30px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QLabel#micDot { color: #ffb020; font-size: 13px; background: transparent; padding: 0 4px; }
QPushButton#drawerArrow { padding: 0; border-radius: 8px; font-size: 16px; color: #22e6ff;
    background: rgba(18, 34, 84, 170); border: 1px solid rgba(34, 230, 255, 170); }
QPushButton#drawerArrow:hover { background: rgba(120, 72, 220, 235); color: #ffffff; }
QListWidget#files { background: rgba(10, 16, 46, 190); border: 1px solid rgba(34, 230, 255, 90); color: #ffffff; }
"""


def window_style(window) -> int:
    if sys.platform != "win32":
        return 0
    return ctypes.windll.user32.GetWindowLongW(int(window.winId()), _GWL_STYLE)


def restore_window_style(window, saved: int) -> None:
    """Qt forgets the title bar once the fullscreen style has been changed, so it is put back here."""
    if sys.platform == "win32" and saved:
        handle = int(window.winId())
        ctypes.windll.user32.SetWindowLongW(handle, _GWL_STYLE, saved & ~_WS_MAXIMIZE)
        ctypes.windll.user32.SetWindowPos(handle, 0, 0, 0, 0, 0, _REFRESH_FRAME)


def keep_menus_in_fullscreen(window) -> None:
    """A thin border stops Windows handing the screen to the graphics chip alone, which hides menus."""
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        handle = int(window.winId())
        user32.SetWindowLongW(handle, _GWL_STYLE, user32.GetWindowLongW(handle, _GWL_STYLE) | _WS_BORDER)
        user32.SetWindowPos(handle, 0, 0, 0, 0, 0, _REFRESH_FRAME)


def dark_title_bar(window) -> None:
    if sys.platform == "win32":
        enabled = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            int(window.winId()), 20, ctypes.byref(enabled), ctypes.sizeof(enabled)
        )
