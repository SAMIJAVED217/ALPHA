"""Asking Grok, through xAI's API with a key kept in the Credential Manager.

Grok helps Gemini with harder thinking, and stands in for a typed message when Gemini
cannot answer. It has no tools, so it can never act on the computer.
"""

from __future__ import annotations

import re

import httpx

from .. import keystore
from .registry import STRING, Tool, ToolRefused, text

KEY_ACCOUNT = "xai-key"
ROOT = "https://api.x.ai/v1"
API = f"{ROOT}/chat/completions"
MODELS = f"{ROOT}/models"
LONGEST_QUESTION = 8_000
LONGEST_ANSWER = 8_000
STAND_IN = (
    "You are answering for APLHA, a personal assistant, because its main AI is unavailable. "
    "You have no tools and cannot touch the computer, so never claim to have done anything. "
    "Keep the answer short, because it may be read aloud."
)

_NOT_FOR_CHAT = re.compile(r"image|imagine|video|voice|tts|embed|vision", re.IGNORECASE)
_LIGHTER_OR_COSTLIER = re.compile(r"fast|mini|code|heavy", re.IGNORECASE)
_VERSION = re.compile(r"grok-(\d+)(?:[.-](\d{1,2})(?!\d))?")


def choose_model(names: list[str]) -> str:
    """The newest full chat model xAI offers this key. Model names change too often to fix one here."""

    def rank(name: str) -> tuple[int, int, bool]:
        version = _VERSION.match(name)
        major, minor = (int(version[1]), int(version[2] or 0)) if version else (0, 0)
        return major, minor, not _LIGHTER_OR_COSTLIER.search(name)

    usable = [name for name in names if name.startswith("grok") and not _NOT_FOR_CHAT.search(name)]
    if not usable:
        raise ToolRefused("xAI did not offer a Grok chat model to this key.")
    return max(usable, key=rank)


class GrokClient:
    def __init__(self, load_key=lambda: keystore.load_secret(KEY_ACCOUNT), transport=None):
        self._load_key = load_key
        self._transport = transport
        self._model = ""

    def connected(self) -> bool:
        try:
            return bool(self._load_key())
        except keystore.KeyStoreError:
            return False

    def _exchange(self, client: httpx.Client, key: str, method: str, address: str, body: dict | None = None) -> dict:
        try:
            response = client.request(method, address, headers={"Authorization": f"Bearer {key}"}, json=body)
        except httpx.HTTPError as error:
            raise ToolRefused(f"Grok could not be reached ({type(error).__name__}).") from error
        if response.status_code >= 400 and "credit" in response.text.lower():
            raise ToolRefused("xAI says this account has no credits left.")
        if response.status_code in (401, 403):
            raise ToolRefused("xAI refused the key. Check it under Settings, Connections.")
        if response.status_code >= 400:
            if response.status_code in (400, 404):
                self._model = ""
            raise ToolRefused(f"xAI answered with error {response.status_code}.")
        try:
            return response.json()
        except ValueError as error:
            raise ToolRefused("xAI sent back something unexpected.") from error

    def _saved_key(self) -> str:
        key = self._load_key()
        if not key:
            raise ToolRefused("Grok is not connected yet. Add an xAI key under Settings, Connections.")
        return key

    def _pick_model(self, client: httpx.Client, key: str) -> None:
        if not self._model:
            listed = self._exchange(client, key, "GET", MODELS)
            offered = listed.get("data") if isinstance(listed, dict) else None
            self._model = choose_model([item.get("id", "") for item in offered or [] if isinstance(item, dict)])

    def check(self) -> None:
        """Proves the key works and that a chat model is available. Spends no credits."""
        self._model = ""
        with httpx.Client(transport=self._transport, timeout=20) as client:
            self._pick_model(client, self._saved_key())

    def ask(self, question: str, instruction: str = "") -> str:
        key = self._saved_key()
        messages = [{"role": "system", "content": instruction}] if instruction else []
        messages.append({"role": "user", "content": question})
        with httpx.Client(transport=self._transport, timeout=60) as client:
            self._pick_model(client, key)
            answer = self._exchange(client, key, "POST", API, {"model": self._model, "messages": messages})
        try:
            return answer["choices"][0]["message"]["content"]
        except (LookupError, TypeError) as error:
            raise ToolRefused("xAI sent back something unexpected.") from error

    def stand_in(self, question: str) -> str:
        return self.ask(question[:LONGEST_QUESTION], STAND_IN)[:LONGEST_ANSWER]


def grok_tools(client: GrokClient | None) -> list[Tool]:
    def ask(arguments):
        answer = client.ask(text(arguments, "question", LONGEST_QUESTION))
        return {"ok": True, "answer": answer[:LONGEST_ANSWER], "detail": "Grok answered."}

    return [
        Tool(
            "ask_grok",
            "Send a question to Grok for harder thinking, or when the owner asks for Grok. "
            "The question leaves this computer for xAI.",
            {"question": (STRING, "The full question, with no private details the owner did not ask to share.")},
            ("question",),
            run=ask,
            reads_outside=True,
        ),
    ]
