"""Gmail, using the owner's own Google OAuth client. Tokens stay in the Credential Manager.

Connecting happens in the owner's browser, against a one-time local page on this
computer. Reading is instant, and sending always needs CONFIRM.
"""

from __future__ import annotations

import base64
import hashlib
import http.server
import re
import secrets
import threading
import urllib.parse
import webbrowser
from email.message import EmailMessage

import httpx

from .. import keystore
from .registry import INTEGER, STRING, Plan, Tool, ToolRefused, text, whole_number

CLIENT_ID_ACCOUNT = "gmail-client-id"
CLIENT_SECRET_ACCOUNT = "gmail-client-secret"
REFRESH_ACCOUNT = "gmail-refresh-token"

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = "https://www.googleapis.com/auth/gmail.readonly https://www.googleapis.com/auth/gmail.send"
SIGN_IN_TIMEOUT_SECONDS = 180
BODY_LIMIT = 12_000

_MESSAGE_ID = re.compile(r"^[0-9a-zA-Z_-]{6,40}$")
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


def is_connected(load=keystore.load_secret) -> bool:
    return bool(load(REFRESH_ACCOUNT))


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def wait_for_code(state: str, on_port) -> str:
    """Serves one local page that receives Google's redirect, then shuts down."""
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if query.get("state", [""])[0] != state:
                self.send_error(400)
                return
            received["code"] = query.get("code", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write("APLHA is connected. You can close this tab.".encode())

        def log_message(self, *_):
            pass

    with http.server.HTTPServer(("127.0.0.1", 0), Handler) as server:
        server.timeout = SIGN_IN_TIMEOUT_SECONDS
        on_port(server.server_port)
        server.handle_request()
    if not received.get("code"):
        raise ToolRefused("Google sign-in did not finish.")
    return received["code"]


def connect(client_id: str, client_secret: str, transport=None, open_browser=webbrowser.open, waiter=wait_for_code) -> None:
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    port = {}

    def start(found_port):
        port["value"] = found_port
        query = urllib.parse.urlencode(
            {
                "client_id": client_id,
                "redirect_uri": f"http://127.0.0.1:{found_port}",
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "access_type": "offline",
                "prompt": "consent",
            }
        )
        open_browser(f"{AUTH_URL}?{query}")

    code = waiter(state, start)
    with httpx.Client(transport=transport, timeout=20) as client:
        response = client.post(
            TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": f"http://127.0.0.1:{port['value']}",
            },
        )
    if response.status_code != 200 or not response.json().get("refresh_token"):
        raise ToolRefused("Google did not give APLHA access. Check the client id and secret.")
    keystore.save_secret(REFRESH_ACCOUNT, response.json()["refresh_token"])


class GmailClient:
    def __init__(self, load=keystore.load_secret, transport=None):
        self._load = load
        self._transport = transport

    def _access_token(self, client: httpx.Client) -> str:
        refresh = self._load(REFRESH_ACCOUNT)
        if not refresh:
            raise ToolRefused("Gmail is not connected yet. Use Settings, Connections.")
        response = client.post(
            TOKEN_URL,
            data={
                "client_id": self._load(CLIENT_ID_ACCOUNT),
                "client_secret": self._load(CLIENT_SECRET_ACCOUNT),
                "refresh_token": refresh,
                "grant_type": "refresh_token",
            },
        )
        if response.status_code != 200:
            raise ToolRefused("Google refused to renew access. Connect Gmail again in Settings.")
        return response.json()["access_token"]

    def request(self, method: str, path: str, **options) -> dict:
        with httpx.Client(transport=self._transport, timeout=20) as client:
            headers = {"Authorization": f"Bearer {self._access_token(client)}"}
            response = client.request(method, f"{API}{path}", headers=headers, **options)
        if response.status_code == 404:
            raise ToolRefused("Gmail could not find that message.")
        if response.status_code >= 400:
            raise ToolRefused(f"Gmail answered with an error ({response.status_code}).")
        return response.json()


def _header(message: dict, name: str) -> str:
    for header in message.get("payload", {}).get("headers", []):
        if header.get("name", "").lower() == name.lower():
            return header.get("value", "")
    return ""


def _plain_text(part: dict) -> str:
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        data = part["body"]["data"]
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")
    return "\n".join(filter(None, (_plain_text(child) for child in part.get("parts", []))))


def _message_id(arguments: dict) -> str:
    value = text(arguments, "message_id", 40)
    if not _MESSAGE_ID.match(value):
        raise ToolRefused("That is not a Gmail message id.")
    return value


def gmail_tools(client: GmailClient) -> list[Tool]:
    def search(arguments):
        limit = whole_number(arguments, "limit", 1, 10) if "limit" in arguments else 5
        query = text(arguments, "query", 200)
        listing = client.request("GET", "/messages", params={"q": query, "maxResults": limit})
        rows = []
        for item in listing.get("messages", []):
            message = client.request(
                "GET", f"/messages/{item['id']}",
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
            )
            rows.append(
                {
                    "message_id": item["id"],
                    "from": _header(message, "From"),
                    "subject": _header(message, "Subject"),
                    "date": _header(message, "Date"),
                    "untrusted_snippet": message.get("snippet", ""),
                }
            )
        return {"ok": True, "messages": rows, "detail": f"Found {len(rows)} emails."}

    def read(arguments):
        message = client.request("GET", f"/messages/{_message_id(arguments)}", params={"format": "full"})
        return {
            "ok": True,
            "from": _header(message, "From"),
            "subject": _header(message, "Subject"),
            "untrusted_email_text": _plain_text(message.get("payload", {}))[:BODY_LIMIT],
            "detail": f"Read the email: {_header(message, 'Subject')}",
        }

    def plan_send(arguments):
        to = text(arguments, "to", 200).strip()
        if not _ADDRESS.match(to):
            raise ToolRefused("That is not a single email address.")
        subject = text(arguments, "subject", 200)
        if re.search(r"[\r\n]", subject):
            raise ToolRefused("A subject cannot contain line breaks.")
        body = text(arguments, "body", 10_000)
        email = EmailMessage()
        email["To"] = to
        email["Subject"] = subject
        email.set_content(body)

        def perform():
            raw = base64.urlsafe_b64encode(email.as_bytes()).decode()
            sent = client.request("POST", "/messages/send", json={"raw": raw})
            if not sent.get("id"):
                raise ToolRefused("Gmail did not confirm the send.")
            return f"Sent the email to {to}."

        return Plan(f"send an email to {to}", f"Subject: {subject}", body, perform)

    return [
        Tool("gmail_search", "Search the owner's Gmail, for example 'is:unread' or 'from:bank'.",
             {"query": (STRING, "A Gmail search."), "limit": (INTEGER, "1 to 10, default 5.")}, ("query",), run=search, reads_outside=True),
        Tool("gmail_read", "Read one email.", {"message_id": (STRING, "From gmail_search.")}, ("message_id",), run=read, reads_outside=True),
        Tool("propose_gmail_send", "Ask the owner to approve sending an email.",
             {"to": (STRING, "One address."), "subject": (STRING, "The subject."), "body": (STRING, "The full text.")},
             ("to", "subject", "body"), propose=plan_send),
    ]
