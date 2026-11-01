"""Computer control tools. `computer` is anything shaped like `WindowsComputer`."""

from __future__ import annotations

import re
import time
from difflib import get_close_matches
from urllib.parse import quote, quote_plus, urlsplit

from dataclasses import dataclass

from .registry import BOOLEAN, INTEGER, STRING, Plan, Tool, ToolRefused, UndoStack, text, whole_number
from .windows_computer import KEY_CODES

WINDOW_PARAM = {"window": (STRING, "Part of the window's title, from list_windows.")}
MOST_LISTED = 40
APP_WAIT_CHECKS = 10
APP_WAIT_SECONDS = 0.5
STEP_SECONDS = 0.5


def _find_window(computer, wanted: str) -> tuple[int, str]:
    wanted = wanted.lower()
    matches = [(handle, title) for handle, title in computer.windows().items() if wanted in title.lower()]
    if not matches:
        raise ToolRefused(f"No open window has '{wanted}' in its title.")
    if len(matches) > 1:
        titles = "; ".join(title for _, title in matches[:8])
        raise ToolRefused(f"Several windows match, so be more exact: {titles}")
    return matches[0]


def _find_app(computer, wanted: str):
    apps = computer.apps()
    by_lower = {name.lower(): name for name in apps}
    lowered = wanted.lower().strip()
    if lowered in by_lower:
        name = by_lower[lowered]
        return name, apps[name]
    containing = sorted((name for key, name in by_lower.items() if lowered in key), key=len)
    if containing:
        return containing[0], apps[containing[0]]
    close = get_close_matches(lowered, by_lower, n=3, cutoff=0.6)
    hint = f" Closest: {', '.join(by_lower[key] for key in close)}." if close else ""
    raise ToolRefused(f"'{wanted}' is not in the Start menu.{hint} Store apps may not be listed.")


def _keys(combo: str) -> list[str]:
    names = [part.strip().lower() for part in combo.split("+")]
    unknown = [name for name in names if name not in KEY_CODES]
    if unknown or not 1 <= len(names) <= 4:
        raise ToolRefused(f"Unknown keys in '{combo}'. Use names like ctrl+s, alt+f4, enter or f5.")
    return names


def _web_address(wanted: str) -> str:
    address = wanted.strip()
    refused = ToolRefused("Only ordinary web addresses can be opened.")
    if re.search(r"[\s\\]", address):
        raise refused
    if "://" not in address:
        # "javascript:x" or "ms-settings:" must stay what they are, not gain https:// in front.
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d)", address):
            raise refused
        address = f"https://{address}"
    parts = urlsplit(address)
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        raise refused
    host = parts.hostname or ""
    if "." not in host and host != "localhost":
        raise refused
    return address


def _find_control(computer, handle: int, wanted: str) -> dict:
    wanted = wanted.lower().strip()
    items = computer.read_window(handle)
    exact = [item for item in items if item["name"].lower() == wanted]
    partial = exact or [item for item in items if wanted in item["name"].lower()]
    if not partial:
        raise ToolRefused(f"Nothing labelled '{wanted}' is visible in that window. Use read_window to see what is.")
    if len(partial) > 1:
        names = "; ".join(item["name"][:50] for item in partial[:6])
        raise ToolRefused(f"Several things match, so be more exact: {names}")
    return partial[0]


@dataclass(frozen=True)
class Messenger:
    name: str
    search_keys: tuple[str, ...]


MESSENGERS = {
    "whatsapp": Messenger("WhatsApp", ("ctrl", "f")),
    "kakaotalk": Messenger("KakaoTalk", ("ctrl", "f")),
    "telegram": Messenger("Telegram", ("ctrl", "k")),
    "discord": Messenger("Discord", ("ctrl", "k")),
    "slack": Messenger("Slack", ("ctrl", "k")),
    "teams": Messenger("Microsoft Teams", ("ctrl", "e")),
}
MESSENGER_NAMES = "One of: " + ", ".join(item.name for item in MESSENGERS.values())


def _messenger(wanted: str) -> Messenger:
    found = next((item for key, item in MESSENGERS.items() if key in wanted.lower().replace(" ", "")), None)
    if found is None:
        raise ToolRefused(f"APLHA has no steps for that app yet. {MESSENGER_NAMES}.")
    return found


def _open_messenger(computer, app: Messenger) -> tuple[int, str]:
    wanted = app.name.lower()
    matches = [(handle, title) for handle, title in computer.windows().items() if wanted in title.lower()]
    exact = [(handle, title) for handle, title in matches if title.lower() == wanted]
    matches = exact or matches
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ToolRefused(f"More than one {app.name} window is open, so please leave just one.")
    raise ToolRefused(f"{app.name} is not open. Use open_app first.")


def _into_window(computer, handle: int, title: str) -> None:
    """The CONFIRM press leaves ALPHA in front, so the target is brought back first."""
    if not computer.focus_window(handle):
        raise ToolRefused(f"Windows would not bring '{title}' to the front, so nothing was sent.")


MEDIA_ACTIONS = ("play_pause", "next", "previous", "stop")
POWER_DELAY_SECONDS = 30
POWER_ACTIONS = {"shutdown": "shut down", "restart": "restart"}


def computer_tools(computer, undo: UndoStack | None = None) -> list[Tool]:
    undo = undo or UndoStack()

    def get_volume(_):
        percent, muted = computer.volume()
        return {"ok": True, "percent": percent, "muted": muted, "detail": f"Volume is {percent}%{' (muted)' if muted else ''}."}

    def set_volume(arguments):
        wanted = whole_number(arguments, "percent", 0, 100)
        before = computer.volume()[0]
        computer.set_volume(wanted)
        percent, muted = computer.volume()
        if abs(percent - wanted) > 1:
            raise ToolRefused(f"Asked for {wanted}%, but the volume reads {percent}%.")
        undo.push(f"volume back to {before}%", lambda: computer.set_volume(before))
        return {"ok": True, "percent": percent, "muted": muted, "detail": f"Volume set to {percent}%{', still muted' if muted else ''}."}

    def set_mute(arguments):
        wanted = arguments.get("muted")
        if not isinstance(wanted, bool):
            raise ToolRefused("muted must be true or false.")
        before = computer.volume()[1]
        computer.set_mute(wanted)
        _, muted = computer.volume()
        if muted != wanted:
            raise ToolRefused("Windows did not change the mute setting.")
        undo.push("mute setting restored", lambda: computer.set_mute(before))
        return {"ok": True, "muted": muted, "detail": "Sound muted." if muted else "Sound unmuted."}

    def get_brightness(_):
        percent = computer.brightness()
        return {"ok": True, "percent": percent, "detail": f"Brightness is {percent}%."}

    def set_brightness(arguments):
        wanted = whole_number(arguments, "percent", 0, 100)
        before = computer.brightness()
        computer.set_brightness(wanted)
        percent = computer.brightness()
        if abs(percent - wanted) > 10:
            raise ToolRefused(f"Asked for {wanted}%, but the brightness reads {percent}%.")
        undo.push(f"brightness back to {before}%", lambda: computer.set_brightness(before))
        return {"ok": True, "percent": percent, "detail": f"Brightness set to {percent}%."}

    def find_apps(arguments):
        wanted = text(arguments, "search", 100).lower()
        names = sorted(name for name in computer.apps() if wanted in name.lower())
        return {"ok": True, "apps": names[:MOST_LISTED], "detail": f"Found {len(names)} matching apps."}

    def open_app(arguments):
        name, link = _find_app(computer, text(arguments, "name", 100))
        before = set(computer.windows())
        computer.open_shortcut(link)
        for _ in range(APP_WAIT_CHECKS):
            time.sleep(APP_WAIT_SECONDS)
            fresh = [title for handle, title in computer.windows().items() if handle not in before]
            if fresh:
                return {"ok": True, "opened": name, "detail": f"Opened {name}. New window: {fresh[0]}."}
        return {
            "ok": True,
            "opened": name,
            "window_seen": False,
            "detail": f"Windows was told to open {name}, but no new window has appeared yet.",
        }

    def list_windows(_):
        titles = list(computer.windows().values())
        return {"ok": True, "windows": titles[:MOST_LISTED], "detail": f"{len(titles)} windows are open."}

    def focus(arguments):
        handle, title = _find_window(computer, text(arguments, "window", 200))
        if not computer.focus_window(handle):
            raise ToolRefused(f"Windows would not bring '{title}' to the front.")
        return {"ok": True, "detail": f"Brought '{title}' to the front."}

    def shower(how, done):
        def run(arguments):
            handle, title = _find_window(computer, text(arguments, "window", 200))
            computer.show_window(handle, how)
            if how != "restore":
                undo.push(f"'{title}' restored", lambda: computer.show_window(handle, "restore"))
            return {"ok": True, "detail": f"{done} '{title}'."}

        return run

    def plan_close(arguments):
        handle, title = _find_window(computer, text(arguments, "window", 200))

        def perform():
            if not computer.close_window(handle):
                raise ToolRefused(f"'{title}' is still open. It may be asking to save.")
            return f"Closed '{title}'."

        return Plan(f"close a window: {title}", "Unsaved work in it could be lost.", title, perform)

    def plan_type(arguments):
        content = text(arguments, "text", 5_000)
        handle, title = _find_window(computer, text(arguments, "window", 200))

        def perform():
            _into_window(computer, handle, title)
            computer.type_text(content)
            return f"Typed {len(content)} characters into '{title}'."

        return Plan(f"type into: {title}", "These exact characters would be typed:", content, perform)

    def plan_keys(arguments):
        combo = text(arguments, "keys", 60)
        names = _keys(combo)
        handle, title = _find_window(computer, text(arguments, "window", 200))

        def perform():
            _into_window(computer, handle, title)
            computer.press_keys(names)
            return f"Pressed {'+'.join(names)} in '{title}'."

        return Plan(f"press {'+'.join(names)} in: {title}", "Keys can trigger anything that window allows.", combo, perform)

    def plan_click(arguments):
        width, height = computer.screen_size()
        x = whole_number(arguments, "x", 0, width - 1)
        y = whole_number(arguments, "y", 0, height - 1)
        right = arguments.get("right") is True
        name = "right-click" if right else "click"

        def perform():
            computer.click(x, y, right)
            return f"Did a {name} at {x}, {y}. APLHA cannot see what it landed on."

        return Plan(f"{name} the mouse at {x}, {y}", "On the main screen.", f"{name} at ({x}, {y})", perform)

    def play_media(arguments):
        wanted = text(arguments, "what", 200)
        if str(arguments.get("app", "youtube")).strip().lower() == "spotify":
            computer.open_url(f"spotify:search:{quote(wanted)}")
            return {"ok": True, "detail": f"Spotify is showing results for {wanted}, but it needs a click to start."}
        video = computer.first_video(wanted)
        if video is None:
            computer.open_url(f"https://www.youtube.com/results?search_query={quote_plus(wanted)}")
            return {"ok": True, "detail": f"No video could be picked, so YouTube is showing results for {wanted}."}
        computer.open_url(f"https://www.youtube.com/watch?v={video}&autoplay=1")
        return {"ok": True, "detail": f"Opened the top YouTube result for {wanted}. Whether it started is not confirmed."}

    def media_control(arguments):
        action = text(arguments, "action", 20).strip().lower()
        if action not in MEDIA_ACTIONS:
            raise ToolRefused(f"action must be one of: {', '.join(MEDIA_ACTIONS)}.")
        computer.media_key(action)
        return {"ok": True, "detail": f"Pressed the {action.replace('_', ' ')} media key."}

    def plan_power(arguments):
        action = text(arguments, "action", 20).strip().lower()
        if action not in POWER_ACTIONS:
            raise ToolRefused("action must be shutdown or restart.")
        word = POWER_ACTIONS[action]

        def perform():
            computer.power(action)
            return f"The computer will {word} in {POWER_DELAY_SECONDS} seconds. Saying cancel stops it."

        return Plan(f"{word} the computer", "Save your work first. Open apps will be closed.", word, perform)

    def cancel_power(_arguments):
        computer.power("cancel")
        return {"ok": True, "detail": "Cancelled the shutdown or restart."}

    def lock(_arguments):
        computer.lock()
        return {"ok": True, "detail": "Locked the computer."}

    def sleep(_arguments):
        computer.sleep()
        return {"ok": True, "detail": "Putting the computer to sleep."}

    def open_website(arguments):
        address = _web_address(text(arguments, "url", 2_000))
        computer.open_url(address)
        return {"ok": True, "detail": f"Asked your browser to open {urlsplit(address).hostname}."}

    def search_web(arguments):
        query = text(arguments, "query", 300)
        computer.open_url(f"https://www.google.com/search?q={quote_plus(query)}")
        return {"ok": True, "detail": f"Searched the web for: {query}"}

    def read_window(arguments):
        handle, title = _find_window(computer, text(arguments, "window", 200))
        items = computer.read_window(handle)
        return {
            "ok": True,
            "window": title,
            "untrusted_window_contents": [{"label": item["name"], "kind": item["kind"]} for item in items],
            "detail": f"Read {len(items)} items in '{title}'.",
        }

    def plan_click_control(arguments):
        handle, title = _find_window(computer, text(arguments, "window", 200))
        wanted = text(arguments, "label", 300)
        found = _find_control(computer, handle, wanted)

        def perform():
            _into_window(computer, handle, title)
            fresh = _find_control(computer, handle, wanted)
            computer.click(fresh["x"], fresh["y"], False)
            return f"Clicked '{fresh['name']}' in '{title}'."

        return Plan(f"click '{found['name'][:60]}'", f"A {found['kind']} in {title}", found["name"], perform)

    def search_chats(app: Messenger, person: str) -> tuple[int, str]:
        """Types the name into the app's search. Never presses Enter, so nothing can be sent."""
        handle, title = _open_messenger(computer, app)
        _into_window(computer, handle, title)
        computer.press_keys(list(app.search_keys))
        time.sleep(STEP_SECONDS)
        computer.type_text(person)
        time.sleep(STEP_SECONDS * 2)
        return handle, title

    def find_chat(arguments):
        app = _messenger(text(arguments, "app", 40))
        person = text(arguments, "person", 100)
        handle, _ = search_chats(app, person)
        return {
            "ok": True,
            "detail": f"Searched {app.name} for {person}. Nothing has been sent.",
            "untrusted_window_contents": [item["name"] for item in computer.read_window(handle)][:60],
        }

    def plan_send_message(arguments):
        app = _messenger(text(arguments, "app", 40))
        person = text(arguments, "person", 100)
        message = text(arguments, "text", 4_000)
        handle, title = search_chats(app, person)

        def perform():
            _into_window(computer, handle, title)
            computer.press_keys(["enter"])
            time.sleep(STEP_SECONDS * 2)
            computer.type_text(message)
            time.sleep(STEP_SECONDS)
            computer.press_keys(["enter"])
            time.sleep(STEP_SECONDS * 2)
            shown = " ".join(item["name"] for item in computer.read_window(handle))
            if message[:60] in shown:
                return f"Sent to {person} on {app.name}, and the message now shows in the chat."
            return f"Pressed send in {app.name}, but APLHA could not see the message in the chat. Please check it."

        return Plan(
            f"send a {app.name} message to {person}",
            f"Make sure {app.name} shows {person} first. The top search result gets opened.",
            message,
            perform,
        )

    def undo_last(_):
        return {"ok": True, "detail": undo.undo()}

    named = {"name": (STRING, "The app's name, for example Notepad.")}
    return [
        Tool("open_website", "Open a web address in the owner's browser.",
             {"url": (STRING, "For example youtube.com.")}, ("url",), run=open_website),
        Tool("search_web", "Search the web in the owner's browser.",
             {"query": (STRING, "What to search for.")}, ("query",), run=search_web),
        Tool("read_window", "Read the labels and text visible in a window, including web pages and chat apps.",
             WINDOW_PARAM, ("window",), run=read_window, reads_outside=True),
        Tool(
            "propose_click_control",
            "Click something by its label, from read_window.",
            {**WINDOW_PARAM, "label": (STRING, "The exact label.")},
            ("window", "label"),
            propose=plan_click_control,
            quick=True,
        ),
        Tool(
            "find_chat",
            "Search for a person in a messaging app, to see if they are there. Sends nothing.",
            {"app": (STRING, MESSENGER_NAMES), "person": (STRING, "Their name in the app.")},
            ("app", "person"),
            run=find_chat,
        ),
        Tool(
            "propose_send_message",
            "Find a person in a messaging app and ask the owner to approve sending them a message.",
            {"app": (STRING, MESSENGER_NAMES), "person": (STRING, "Their name."), "text": (STRING, "The exact message.")},
            ("app", "person", "text"),
            propose=plan_send_message,
        ),
        Tool(
            "play_media",
            "Play a song or video by name, on YouTube in the browser unless the owner says Spotify.",
            {"what": (STRING, "The song, artist or video."), "app": (STRING, "youtube or spotify.")},
            ("what",),
            run=play_media,
        ),
        Tool("media_control", "Pause, resume, skip or stop whatever is playing.",
             {"action": (STRING, "play_pause, next, previous or stop.")}, ("action",), run=media_control),
        Tool("propose_power_off", "Shut down or restart the computer, only when the owner asks.",
             {"action": (STRING, "shutdown or restart.")}, ("action",), propose=plan_power),
        Tool("cancel_power_off", "Stop a shutdown or restart that is counting down.", run=cancel_power),
        Tool("lock_computer", "Lock the computer, only when the owner asks.", run=lock),
        Tool("sleep_computer", "Put the computer to sleep, only when the owner asks.", run=sleep),
        Tool("undo_last_action", "Undo the most recent volume, brightness, window, file or memory change.", run=undo_last),
        Tool("get_volume", "Read the speaker volume and whether it is muted.", run=get_volume),
        Tool("set_volume", "Set the speaker volume.", {"percent": (INTEGER, "0 to 100.")}, ("percent",), run=set_volume),
        Tool("set_mute", "Mute or unmute the speakers.", {"muted": (BOOLEAN, "True to mute.")}, ("muted",), run=set_mute),
        Tool("get_brightness", "Read the built-in screen's brightness.", run=get_brightness),
        Tool("set_brightness", "Set the built-in screen's brightness.", {"percent": (INTEGER, "0 to 100.")}, ("percent",), run=set_brightness),
        Tool("find_apps", "Search the Start menu for installed apps.", {"search": (STRING, "Part of a name.")}, ("search",), run=find_apps),
        Tool("open_app", "Open an app from the Start menu.", named, ("name",), run=open_app),
        Tool("list_windows", "List the titles of open windows.", run=list_windows),
        Tool("focus_window", "Bring a window to the front.", WINDOW_PARAM, ("window",), run=focus),
        Tool("minimize_window", "Minimise a window.", WINDOW_PARAM, ("window",), run=shower("minimize", "Minimised")),
        Tool("maximize_window", "Maximise a window.", WINDOW_PARAM, ("window",), run=shower("maximize", "Maximised")),
        Tool("restore_window", "Restore a minimised window.", WINDOW_PARAM, ("window",), run=shower("restore", "Restored")),
        Tool("propose_close_window", "Close a window.", WINDOW_PARAM, ("window",), propose=plan_close, quick=True),
        Tool(
            "propose_type_text",
            "Type text into a window.",
            {**WINDOW_PARAM, "text": (STRING, "The exact text to type.")},
            ("window", "text"),
            propose=plan_type,
            quick=True,
        ),
        Tool(
            "propose_press_keys",
            "Press keys in a window.",
            {**WINDOW_PARAM, "keys": (STRING, "For example ctrl+s, alt+f4 or enter.")},
            ("window", "keys"),
            propose=plan_keys,
            quick=True,
        ),
        Tool(
            "propose_mouse_click",
            "Ask the owner to approve a mouse click.",
            {
                "x": (INTEGER, "Pixels from the left of the main screen."),
                "y": (INTEGER, "Pixels from the top."),
                "right": (BOOLEAN, "True for a right-click."),
            },
            ("x", "y"),
            propose=plan_click,
        ),
    ]
