"""Lets the owner's phone or tablet talk to APLHA through a small page on the same network.

It is off until the owner turns it on, and turning it off forgets every device. A device
joins with a short one-time code shown on the PC. It can send messages and stop a reply,
but nothing here can press CONFIRM, because that needs a real press on the PC itself.
Requests are accepted only from private addresses, and the page has no secrets of its own.
"""

from __future__ import annotations

import hashlib
import http.server
import ipaddress
import json
import logging
import secrets
import socket
import threading
import time
from collections import deque
from typing import Callable
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

PORT = 8765
CODE_SECONDS = 300
MOST_WRONG_CODES = 5
SEEN_SECONDS = 10
LONGEST_REQUEST = 8_192
LONGEST_MESSAGE = 2_000
EVENTS_KEPT = 300
COOKIE = "aplha_device"
_TAILSCALE = ipaddress.ip_network("100.64.0.0/10")

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>APLHA</title>
<style>
:root { color-scheme: dark; }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { background: #05070d; color: #dfe9f7; font: 17px/1.45 system-ui, sans-serif;
       display: flex; flex-direction: column; padding: 16px; gap: 12px; }
h1 { font-size: 20px; letter-spacing: .3em; margin: 0; color: #7fc8ff; }
#state { font-size: 14px; color: #8fa3bd; }
#log { flex: 1; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }
.line { padding: 9px 12px; border-radius: 10px; background: #101725; white-space: pre-wrap; overflow-wrap: anywhere; }
.you { background: #16345a; align-self: flex-end; }
.notice { background: none; color: #8fa3bd; font-size: 14px; padding: 2px 4px; }
form { display: flex; gap: 8px; }
input { flex: 1; min-width: 0; padding: 12px; font-size: 17px; border-radius: 10px; color: inherit;
        background: #101725; border: 1px solid #2b3a52; }
button { padding: 12px 16px; font-size: 16px; border: 0; border-radius: 10px; color: #05070d; background: #7fc8ff; }
#stop { background: #2b3a52; color: #dfe9f7; }
[hidden] { display: none !important; }
</style></head><body>
<h1>APLHA</h1>
<div id="state"></div>
<form id="pair"><input id="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6"
placeholder="Code shown on your PC" aria-label="Code shown on your PC"><button>Join</button></form>
<div id="log" hidden></div>
<form id="talk" hidden><input id="text" maxlength="2000" placeholder="Message APLHA" aria-label="Message APLHA">
<button>Send</button><button type="button" id="stop">Stop</button></form>
<script>
const $ = id => document.getElementById(id);
let after = 0, open = null, joined = false;
function add(kind, text) {
  const line = document.createElement("div");
  line.className = "line " + kind;
  line.textContent = text;
  $("log").append(line);
  $("log").scrollTop = $("log").scrollHeight;
  return line;
}
function post(path, body) {
  return fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})});
}
function show(paired) {
  joined = paired;
  $("pair").hidden = paired;
  $("log").hidden = $("talk").hidden = !paired;
}
async function poll() {
  try {
    const answer = await fetch("/events?after=" + after);
    if (answer.status === 401) { show(false); $("state").textContent = "Enter the code shown on your PC."; return; }
    const data = await answer.json();
    if (!joined) show(true);
    for (const event of data.events) {
      after = event.id;
      if (event.kind === "reply") {
        open = open || add("", "");
        open.textContent += event.text;
        $("log").scrollTop = $("log").scrollHeight;
      } else if (event.kind === "done") { open = null; }
      else if (event.kind === "state") { $("state").textContent = event.text; }
      else { open = null; add("notice", event.text); }
    }
  } catch (error) { $("state").textContent = "APLHA cannot be reached."; }
}
$("pair").onsubmit = async event => {
  event.preventDefault();
  const answer = await post("/pair", {code: $("code").value});
  $("code").value = "";
  if (answer.ok) { await poll(); } else { $("state").textContent = (await answer.json()).error; }
};
$("talk").onsubmit = async event => {
  event.preventDefault();
  const text = $("text").value.trim();
  if (!text) return;
  $("text").value = "";
  open = null;
  add("you", text);
  const answer = await post("/send", {text});
  if (!answer.ok) add("notice", "That message did not reach APLHA.");
};
$("stop").onclick = () => post("/stop");
poll();
setInterval(() => { if (joined) poll(); }, 1500);
</script></body></html>
"""


def addresses() -> list[str]:
    """The addresses a phone can use. Asking for a route sends nothing."""
    found = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("10.255.255.255", 1))
            found.append(probe.getsockname()[0])
    except OSError:
        pass
    try:
        known = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        known = []
    found += [item for item in known if item not in found and ipaddress.ip_address(item) in _TAILSCALE]
    return found


def allowed_peer(address: str) -> bool:
    try:
        peer = ipaddress.ip_address(address)
    except ValueError:
        return False
    return peer.is_private or peer in _TAILSCALE


class RemoteControl:
    """Knows who may join and what they may see. Holds nothing on disk."""

    def __init__(self, on_message: Callable[[str], None], on_stop: Callable[[], None], clock=time.monotonic):
        self._on_message = on_message
        self._on_stop = on_stop
        self._clock = clock
        self._lock = threading.Lock()
        self._code = ""
        self._code_ends = 0.0
        self._wrong = 0
        self._devices: set[str] = set()
        self._events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self._next_event = 1
        self._last_seen = None

    def new_code(self) -> str:
        with self._lock:
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._code_ends = self._clock() + CODE_SECONDS
            self._wrong = 0
            return self._code

    def forget_everyone(self) -> None:
        with self._lock:
            self._code = ""
            self._devices.clear()
            self._events.clear()
            self._last_seen = None

    def pair(self, code: str) -> str:
        """Gives a device its token, or raises PermissionError with a plain reason."""
        with self._lock:
            if not self._code or self._clock() > self._code_ends:
                self._code = ""
                raise PermissionError("That code has run out. Ask for a new one on your PC.")
            if not secrets.compare_digest(code.encode(), self._code.encode()):
                self._wrong += 1
                if self._wrong >= MOST_WRONG_CODES:
                    self._code = ""
                raise PermissionError("That code is not right.")
            self._code = ""
            token = secrets.token_urlsafe(32)
            self._devices.add(_hashed(token))
            return token

    def knows(self, token: str) -> bool:
        with self._lock:
            known = bool(token) and _hashed(token) in self._devices
            if known:
                self._last_seen = self._clock()
            return known

    def connected(self) -> bool:
        with self._lock:
            return self._last_seen is not None and self._clock() - self._last_seen < SEEN_SECONDS

    def push(self, kind: str, text: str = "") -> None:
        with self._lock:
            if self._devices:
                self._events.append({"id": self._next_event, "kind": kind, "text": text})
                self._next_event += 1

    def events_after(self, seen: int) -> list[dict]:
        with self._lock:
            return [event for event in self._events if event["id"] > seen]

    def message(self, text: str) -> None:
        self._on_message(text)

    def stop_reply(self) -> None:
        self._on_stop()


def _hashed(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class _Handler(http.server.BaseHTTPRequestHandler):
    timeout = 10
    control: RemoteControl

    def setup(self) -> None:
        super().setup()
        self.server_version = "APLHA"
        self.sys_version = ""

    def log_message(self, *_) -> None:
        pass

    def _answer(self, status: int, body: bytes, kind: str, extra: tuple[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'")
        if extra:
            self.send_header(*extra)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, data: dict, extra: tuple[str, str] | None = None) -> None:
        self._answer(status, json.dumps(data).encode(), "application/json", extra)

    def _trusted_request(self) -> bool:
        """Blocks other networks, and websites trying to reach this page through the owner's browser."""
        if not allowed_peer(self.client_address[0]):
            return False
        host = self.headers.get("Host", "")
        try:
            ipaddress.ip_address(urlparse(f"//{host}").hostname or "")
        except ValueError:
            return False
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).netloc == host

    def _token(self) -> str:
        for piece in self.headers.get("Cookie", "").split(";"):
            name, _, value = piece.strip().partition("=")
            if name == COOKIE:
                return value
        return ""

    def _body(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return None
        if not 0 <= length <= LONGEST_REQUEST or self.headers.get_content_type() != "application/json":
            return None
        try:
            body = json.loads(self.rfile.read(length))
        except (ValueError, OSError):
            return None
        return body if isinstance(body, dict) else None

    def do_GET(self) -> None:
        if not self._trusted_request():
            self._json(403, {"error": "Not allowed."})
            return
        address = urlparse(self.path)
        if address.path == "/":
            self._answer(200, PAGE.encode(), "text/html; charset=utf-8")
        elif address.path != "/events":
            self._json(404, {"error": "Not found."})
        elif not self.control.knows(self._token()):
            self._json(401, {"error": "Join first."})
        else:
            seen = parse_qs(address.query).get("after", ["0"])[0]
            self._json(200, {"events": self.control.events_after(int(seen) if seen.isdecimal() else 0)})

    def do_POST(self) -> None:
        if not self._trusted_request():
            self._json(403, {"error": "Not allowed."})
            return
        body = self._body()
        if body is None:
            self._json(400, {"error": "That request could not be read."})
        elif self.path == "/pair":
            self._pair(body)
        elif not self.control.knows(self._token()):
            self._json(401, {"error": "Join first."})
        elif self.path == "/send":
            self._send(body)
        elif self.path == "/stop":
            self.control.stop_reply()
            self._json(200, {"ok": True})
        else:
            self._json(404, {"error": "Not found."})

    def _pair(self, body: dict) -> None:
        try:
            token = self.control.pair(str(body.get("code", "")))
        except PermissionError as error:
            self._json(403, {"error": str(error)})
            return
        cookie = f"{COOKIE}={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age=2592000"
        self._json(200, {"ok": True}, ("Set-Cookie", cookie))

    def _send(self, body: dict) -> None:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > LONGEST_MESSAGE:
            self._json(400, {"error": "That message cannot be sent."})
            return
        self.control.message(text.strip())
        self._json(200, {"ok": True})


class RemoteServer:
    def __init__(self, control: RemoteControl, host: str = "0.0.0.0", port: int = PORT):
        self.control = control
        self._host = host
        self._port = port
        self._server: http.server.ThreadingHTTPServer | None = None

    @property
    def running(self) -> bool:
        return self._server is not None

    @property
    def port(self) -> int:
        return self._server.server_port if self._server else self._port

    def start(self) -> None:
        handler = type("Handler", (_Handler,), {"control": self.control})
        self._server = http.server.ThreadingHTTPServer((self._host, self._port), handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="alpha-remote", daemon=True).start()
        log.info("Phone control turned on")

    def stop(self) -> None:
        self.control.forget_everyone()
        server, self._server = self._server, None
        if server is not None:
            server.shutdown()
            server.server_close()
            log.info("Phone control turned off")
