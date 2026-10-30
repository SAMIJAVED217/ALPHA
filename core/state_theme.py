"""
core/state_theme.py — the colour the HUD wears in each state.

Pure data and arithmetic, with no Qt import, so it can be tested without a
window. Colours can be overridden from config/api_keys.json:

    "state_colors": {"THINKING": "#ff00aa"}

Only the keys present are replaced. A value that is not a #rrggbb colour is
ignored rather than raised, because a typo in a config file must not stop the
HUD from drawing.
"""
from __future__ import annotations

import re

DEFAULTS = {
    "IDLE":       "#3f7bff",   # subtle blue
    "LISTENING":  "#22e6ff",   # cyan
    "THINKING":   "#9b5cff",   # violet
    "SPEAKING":   "#2f8dff",   # electric blue
    "VISION":     "#14d6c0",   # teal
    "PROCESSING": "#b14dff",   # purple
    "WARNING":    "#ffb020",   # amber
    "ERROR":      "#ff3b4e",   # red
    "MUTED":      "#64748b",   # slate: muted is a choice, not a fault
}

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def palette(overrides=None) -> dict:
    out = dict(DEFAULTS)
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            k = str(key).strip().upper()
            if k in out and isinstance(value, str) and _HEX.match(value.strip()):
                out[k] = value.strip().lower()
    return out


def key_for(state: str, speaking: bool = False, muted: bool = False,
            alert: str = "") -> str:
    """Which colour applies now. An alert outranks everything, because it is
    what the user needs to see: a failed connection or a waiting confirmation."""
    alert = str(alert or "").upper()
    if alert in ("WARNING", "ERROR"):
        return alert
    if muted:
        return "MUTED"
    if speaking:
        return "SPEAKING"
    state = str(state or "").upper()
    return state if state in DEFAULTS else "IDLE"


def rgb(hex_colour: str) -> tuple:
    h = hex_colour.lstrip("#")
    return (float(int(h[0:2], 16)), float(int(h[2:4], 16)), float(int(h[4:6], 16)))


def approach(current: tuple, target: tuple, rate: float = 0.12) -> tuple:
    """One smoothing step. Stepped at 60 Hz, 0.12 settles in about 0.4 seconds."""
    return tuple(c + (t - c) * rate for c, t in zip(current, target))
