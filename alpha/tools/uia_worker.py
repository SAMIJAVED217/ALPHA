"""Reads what a window contains through Windows accessibility, in its own process.

Usage: python -m alpha.tools.uia_worker <window handle>. Writes one JSON list to
standard output, of the labelled things visible in that window. This only reads.
"""

from __future__ import annotations

import ctypes
import json
import sys
from _ctypes import COMError

MOST_ITEMS = 250
LONGEST_NAME = 300
_PER_MONITOR_AWARE = 2
_TREE_DESCENDANTS = 4
_NAME, _CONTROL_TYPE, _RECTANGLE, _OFFSCREEN = 30005, 30003, 30001, 30022
_TYPE_NAMES = {
    50000: "button", 50002: "checkbox", 50003: "dropdown", 50004: "edit", 50005: "link", 50007: "list item",
    50008: "list", 50011: "menu item", 50019: "tab", 50020: "text", 50025: "custom", 50026: "group", 50030: "document",
}


def read(handle: int) -> list[dict]:
    ctypes.windll.shcore.SetProcessDpiAwareness(_PER_MONITOR_AWARE)
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as uia

    automation = comtypes.client.CreateObject(uia.CUIAutomation, interface=uia.IUIAutomation)
    root = automation.ElementFromHandle(handle)
    cache = automation.CreateCacheRequest()
    for identifier in (_NAME, _CONTROL_TYPE, _RECTANGLE, _OFFSCREEN):
        cache.AddProperty(identifier)
    found = root.FindAllBuildCache(_TREE_DESCENDANTS, automation.CreateTrueCondition(), cache)

    items = []
    seen = set()
    for index in range(found.Length):
        # An app can drop an element while it is being read, which should only cost that element.
        try:
            element = found.GetElement(index)
            name = (element.CachedName or "").strip()
            if not name or element.CachedIsOffscreen:
                continue
            rectangle = element.CachedBoundingRectangle
            kind = _TYPE_NAMES.get(element.CachedControlType, "other")
        except COMError:
            continue
        width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
        if width <= 0 or height <= 0 or kind in ("group", "custom") or (name, kind) in seen:
            continue
        seen.add((name, kind))
        items.append(
            {
                "name": name[:LONGEST_NAME],
                "kind": kind,
                "x": rectangle.left + width // 2,
                "y": rectangle.top + height // 2,
            }
        )
        if len(items) >= MOST_ITEMS:
            break
    return items


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(read(int(sys.argv[1])), ensure_ascii=False))
