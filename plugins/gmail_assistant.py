"""
[ALPHA] Gmail — search, read, draft, and send (send only after you press CONFIRM).

Setup (once):
    ⚙ SETUP → PLUGIN SETTINGS → GMAIL → "SIGN IN TO GMAIL".
    A browser tab opens; pick your Google account and allow access.
    The sign-in is saved to plugins/gmail_token.json (treat it like a password).

Uses the OAuth desktop client in plugins/credentials.json (your own Google Cloud
project). Scopes: read mail + create drafts / send. Nothing is ever deleted,
archived or relabelled by this plugin.
"""

import base64
import html
import os
import re
import threading
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr

PLUGIN = {
    "name": "gmail_assistant",
    "description": (
        "The user's Gmail inbox. action='search' (default) lists matching emails "
        "using Gmail search syntax in 'query' (e.g. 'is:unread', 'newer_than:2d', "
        "'from:professor', 'subject:exam'); it returns ids. action='read' returns "
        "one email in full by 'message_id' (from a search). action='draft' saves "
        "a draft in Gmail ('to', 'subject', 'body'; set 'reply_to_id' to reply "
        "in the same thread). action='send' sends an email: it puts a CONFIRM "
        "button on the HUD and only sends after the user presses it. Prefer "
        "'draft' unless the user clearly says send. Never guess an email "
        "address: if the user names a person, search past emails for their "
        "address and say it back before drafting or sending. Use this, not "
        "send_message, for email."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "search | read | draft | send. Default search.",
            },
            "query": {
                "type": "STRING",
                "description": "Gmail search query for action=search. Default 'newer_than:7d'.",
            },
            "max_results": {
                "type": "INTEGER",
                "description": "How many emails to list for search, 1-10. Default 5.",
            },
            "message_id": {
                "type": "STRING",
                "description": "Email id for action=read (from a previous search).",
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address(es), comma-separated. Not needed when replying.",
            },
            "cc": {"type": "STRING", "description": "Optional CC address(es), comma-separated."},
            "subject": {"type": "STRING", "description": "Subject line for draft/send."},
            "body": {"type": "STRING", "description": "Plain-text email body for draft/send."},
            "reply_to_id": {
                "type": "STRING",
                "description": "Email id being replied to (keeps the thread, fills recipient and subject).",
            },
        },
        "required": [],
    },
    "tier": "extra",
    "brief": "Gmail: search, read, draft, send (send needs on-screen confirm).",
}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "gmail_token.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",   # drafts + send
]

_NS = "gmail_assistant"
_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_signin_lock = threading.Lock()
_signin_running = False


# ── sign-in ───────────────────────────────────────────────────────────────────

def _load_creds():
    """Saved credentials that are valid (refreshing if needed), or None."""
    if not os.path.exists(TOKEN_FILE):
        return None
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    try:
        # No scopes argument on purpose: we want the scopes that were actually
        # granted, so a token from the old read-only plugin is not mistaken for
        # one that can draft and send.
        creds = Credentials.from_authorized_user_file(TOKEN_FILE)
    except Exception:
        return None
    if not creds.has_scopes(SCOPES):
        return None
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            # Typically "invalid_grant": the sign-in expired (7 days while the
            # Google project is in Testing) or access was revoked. Forget it so
            # the next sign-in starts clean.
            try:
                os.remove(TOKEN_FILE)
            except OSError:
                pass
            return None
        _save(creds)
        return creds
    return None


def _save(creds):
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


def _sign_in(timeout=180):
    """Run Google's browser sign-in. Returns (ok, message)."""
    global _signin_running
    if not os.path.exists(CREDENTIALS_FILE):
        return False, "credentials.json is missing from the plugins folder."
    with _signin_lock:
        if _signin_running:
            return False, "A Gmail sign-in is already open in your browser."
        _signin_running = True
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
        creds = flow.run_local_server(
            port=0,
            open_browser=True,
            timeout_seconds=timeout,
            authorization_prompt_message="",
            success_message="ALPHA is connected to Gmail. You can close this tab.",
        )
        _save(creds)
        try:
            from googleapiclient.discovery import build
            svc = build("gmail", "v1", credentials=creds, cache_discovery=False)
            addr = svc.users().getProfile(userId="me").execute().get("emailAddress", "")
        except Exception:
            addr = ""
        return True, f"Signed in{(' as ' + addr) if addr else ''}."
    except Exception as e:
        name = type(e).__name__
        if "Timeout" in name:
            return False, "Sign-in timed out — press the button and finish it in the browser."
        if "access_denied" in str(e):
            return False, "Sign-in was cancelled in the browser."
        return False, f"Sign-in failed: {e}"
    finally:
        with _signin_lock:
            _signin_running = False


def _settings_sign_in(_values):
    return _sign_in()


PLUGIN_SETTINGS = {
    "title": "GMAIL",
    "fields": [
        {
            "key": "allow_send",
            "type": "toggle",
            "label": "Allow sending (always asks you to CONFIRM first)",
            "default": True,
        },
    ],
    "action": {"label": "SIGN IN TO GMAIL", "run": _settings_sign_in},
}


def _service():
    creds = _load_creds()
    if creds is None:
        return None
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ── message helpers ───────────────────────────────────────────────────────────

def _headers(msg):
    return {
        h["name"].lower(): h["value"]
        for h in msg.get("payload", {}).get("headers", [])
        if "name" in h and "value" in h
    }


def _b64(data):
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def _html_to_text(markup):
    try:
        from bs4 import BeautifulSoup
        return BeautifulSoup(markup, "html.parser").get_text("\n")
    except Exception:
        markup = re.sub(r"(?is)<(script|style).*?</\1>", " ", markup)
        markup = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", markup)
        return html.unescape(re.sub(r"<[^>]+>", " ", markup))


def _body_text(payload):
    plain, rich = [], []

    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_b64(data))
        elif data and mime == "text/html":
            rich.append(_b64(data))
        for child in part.get("parts", []) or []:
            walk(child)

    walk(payload)
    text = "\n".join(plain).strip() or _html_to_text("\n".join(rich)).strip()
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _who(value):
    name, addr = parseaddr(value or "")
    if name and addr:
        return f"{name} <{addr}>"
    return addr or name or "Unknown sender"


def _addresses(value):
    out = []
    for _, addr in getaddresses([value or ""]):
        addr = addr.strip()
        if addr:
            out.append(addr)
    return out


def _bad_addresses(addrs):
    return [a for a in addrs if not _EMAIL_RE.match(a)]


# ── actions ───────────────────────────────────────────────────────────────────

def _search(svc, query, n):
    res = svc.users().messages().list(userId="me", q=query, maxResults=n).execute()
    items = res.get("messages", [])
    if not items:
        return f"No emails match: {query}"
    lines = []
    for it in items:
        m = svc.users().messages().get(
            userId="me", id=it["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]).execute()
        h = _headers(m)
        unread = "UNREAD · " if "UNREAD" in m.get("labelIds", []) else ""
        lines.append(
            f"[id {m['id']}] {unread}{h.get('date', '')}\n"
            f"From: {_who(h.get('from'))}\n"
            f"Subject: {h.get('subject', '(no subject)')}\n"
            f"Preview: {html.unescape(m.get('snippet', ''))}"
        )
    return (f"{len(lines)} email(s) for '{query}'. Summarise them briefly; use "
            f"action=read with an id for the full text.\n\n" + "\n\n".join(lines))


def _read(svc, message_id):
    m = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    h = _headers(m)
    body = _body_text(m.get("payload", {})) or "[no readable text in this email]"
    if len(body) > 6000:
        body = body[:6000] + "\n… [truncated]"
    attach = []

    def walk(part):
        if part.get("filename"):
            attach.append(part["filename"])
        for child in part.get("parts", []) or []:
            walk(child)

    walk(m.get("payload", {}))
    extra = f"\nAttachments: {', '.join(attach)}" if attach else ""
    return (f"From: {_who(h.get('from'))}\nTo: {h.get('to', '')}\n"
            f"Date: {h.get('date', '')}\nSubject: {h.get('subject', '(no subject)')}"
            f"{extra}\n\n{body}")


def _compose(svc, p):
    """Build the outgoing message. Returns (raw_b64, thread_id, to_list, subject) or raises ValueError."""
    to = _addresses(p.get("to", ""))
    cc = _addresses(p.get("cc", ""))
    subject = str(p.get("subject", "") or "").strip()
    body = str(p.get("body", "") or "").strip()
    thread_id = None
    msg = EmailMessage()

    reply_id = str(p.get("reply_to_id", "") or "").strip()
    if reply_id:
        orig = svc.users().messages().get(
            userId="me", id=reply_id, format="metadata",
            metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"]).execute()
        oh = _headers(orig)
        thread_id = orig.get("threadId")
        if not to:
            to = _addresses(oh.get("reply-to") or oh.get("from", ""))
        if not subject:
            s = oh.get("subject", "")
            subject = s if s.lower().startswith("re:") else f"Re: {s}"
        mid = oh.get("message-id")
        if mid:
            msg["In-Reply-To"] = mid
            msg["References"] = (oh.get("references", "") + " " + mid).strip()

    if not to:
        raise ValueError("Who should it go to? I need an email address.")
    bad = _bad_addresses(to + cc)
    if bad:
        raise ValueError(f"These don't look like email addresses: {', '.join(bad)}")
    if not body:
        raise ValueError("The email body is empty.")

    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    return raw, thread_id, to + cc, msg["Subject"]


def _draft(svc, p):
    raw, thread_id, rcpt, subject = _compose(svc, p)
    m = {"raw": raw}
    if thread_id:
        m["threadId"] = thread_id
    svc.users().drafts().create(userId="me", body={"message": m}).execute()
    return (f"Draft saved in Gmail → Drafts. To: {', '.join(rcpt)}. "
            f"Subject: {subject}. Tell the user it is waiting in Drafts, not sent.")


def _send(svc, p, player):
    from memory.config_manager import get_plugin_setting
    if get_plugin_setting(_NS, "allow_send", True) is False:
        return ("Sending is switched off in Plugin Settings → Gmail. " + _draft(svc, p))
    raw, thread_id, rcpt, subject = _compose(svc, p)
    from core import confirm
    waiting = confirm.pending_title()
    if waiting:
        return f"Another confirmation is already on screen ({waiting}). Nothing was sent."

    def do_send():
        m = {"raw": raw}
        if thread_id:
            m["threadId"] = thread_id
        s = _service()
        if s is None:
            return "Gmail sign-in expired; the email was NOT sent."
        s.users().messages().send(userId="me", body=m).execute()
        return f"Email sent to {', '.join(rcpt)}."

    preview = str(p.get("body", "")).strip().replace("\n", " ")
    if len(preview) > 220:
        preview = preview[:220] + "…"
    return confirm.request(
        key="gmail_send",
        title=f"Send email to {', '.join(rcpt)}"[:90],
        detail=f"Subject: {subject}\n{preview}",
        run=do_send,
    )


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "search") or "search").strip().lower()
    try:
        svc = _service()
        if svc is None:
            if not os.path.exists(CREDENTIALS_FILE):
                return ("Gmail isn't set up: credentials.json is missing from the "
                        "plugins folder.")
            threading.Thread(target=_sign_in, daemon=True, name="gmail-signin").start()
            return ("Gmail isn't signed in yet, so I opened Google sign-in in the "
                    "browser. Ask the user to approve it there, then try again.")

        if action == "read":
            mid = str(p.get("message_id", "") or "").strip()
            if not mid:
                return "Which email? Search first, then read it by its id."
            out = _read(svc, mid)
        elif action == "draft":
            out = _draft(svc, p)
        elif action == "send":
            out = _send(svc, p, player)
        else:
            query = str(p.get("query", "") or "").strip() or "newer_than:7d"
            try:
                n = int(p.get("max_results", 5))
            except (TypeError, ValueError):
                n = 5
            out = _search(svc, query, max(1, min(n, 10)))

        if player:
            try:
                player.write_log(f"SYS: Gmail — {action}.")
            except Exception:
                pass
        return out
    except ValueError as e:
        return str(e)
    except Exception as e:
        text = str(e)
        if "invalid_grant" in text or "401" in text:
            try:
                os.remove(TOKEN_FILE)
            except OSError:
                pass
            return "The Gmail sign-in has expired. Open Plugin Settings → Gmail and sign in again."
        return f"Gmail error: {text[:300]}"
