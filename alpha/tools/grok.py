"""The second AI: Grok through xAI, or free models through Groq, a different company.

Keys are kept in the Credential Manager, and xAI is used when both are saved. The second
AI helps Gemini with harder thinking, and stands in for a typed message when Gemini
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
GROQ_KEY_ACCOUNT = "groq-key"
GROQ_ROOT = "https://api.groq.com/openai/v1"
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


_GROQ_NOT_FOR_CHAT = re.compile(r"whisper|tts|guard|embed|orpheus|playai|compound", re.IGNORECASE)


def choose_groq_model(names: list[str]) -> str:
    """Groq's general chat models are named 'versatile'. Its list changes too, so none is fixed here."""
    usable = [name for name in names if not _GROQ_NOT_FOR_CHAT.search(name)]
    if not usable:
        raise ToolRefused("Groq did not offer a chat model to this key.")
    return max(usable, key=lambda name: ("versatile" in name, name))


class GrokClient:
    def __init__(self, load_key=lambda: keystore.load_secret(KEY_ACCOUNT), transport=None,
                 root=ROOT, name="Grok", picker=choose_model):
        self._load_key = load_key
        self._transport = transport
        self._root = root
        self.name = name
        self._picker = picker
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
            raise ToolRefused(f"{self.name} could not be reached ({type(error).__name__}).") from error
        if response.status_code >= 400 and "credit" in response.text.lower():
            raise ToolRefused(f"{self.name} says this account has no credits left.")
        if response.status_code in (401, 403):
            raise ToolRefused(f"{self.name} refused the key. Check it under Settings, Connections.")
        if response.status_code == 429:
            raise ToolRefused(f"{self.name} says the usage limit has been reached. Try again later.")
        if response.status_code >= 400:
            if response.status_code in (400, 404):
                self._model = ""
            raise ToolRefused(f"{self.name} answered with error {response.status_code}.")
        try:
            return response.json()
        except ValueError as error:
            raise ToolRefused(f"{self.name} sent back something unexpected.") from error

    def _saved_key(self) -> str:
        key = self._load_key()
        if not key:
            raise ToolRefused(f"{self.name} is not connected yet. Add its key under Settings, Connections.")
        return key

    def _pick_model(self, client: httpx.Client, key: str) -> None:
        if not self._model:
            listed = self._exchange(client, key, "GET", f"{self._root}/models")
            offered = listed.get("data") if isinstance(listed, dict) else None
            self._model = self._picker([item.get("id", "") for item in offered or [] if isinstance(item, dict)])

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
            answer = self._exchange(
                client, key, "POST", f"{self._root}/chat/completions", {"model": self._model, "messages": messages}
            )
        try:
            return answer["choices"][0]["message"]["content"]
        except (LookupError, TypeError) as error:
            raise ToolRefused(f"{self.name} sent back something unexpected.") from error

    def stand_in(self, question: str) -> str:
        return self.ask(question[:LONGEST_QUESTION], STAND_IN)[:LONGEST_ANSWER]


def groq_client(load_key=lambda: keystore.load_secret(GROQ_KEY_ACCOUNT), transport=None) -> GrokClient:
    return GrokClient(load_key, transport, GROQ_ROOT, "Groq", choose_groq_model)


class SecondAI:
    """Uses xAI when its key is saved, otherwise Groq."""

    def __init__(self, xai: GrokClient | None = None, groq: GrokClient | None = None):
        self._xai = xai or GrokClient()
        self._groq = groq or groq_client()

    def _chosen(self) -> GrokClient:
        return self._xai if self._xai.connected() else self._groq

    @property
    def name(self) -> str:
        return self._chosen().name

    def connected(self) -> bool:
        return self._chosen().connected()

    def ask(self, question: str, instruction: str = "") -> str:
        return self._chosen().ask(question, instruction)

    def stand_in(self, question: str) -> str:
        return self._chosen().stand_in(question)


def grok_tools(client) -> list[Tool]:
    def ask(arguments):
        answer = client.ask(text(arguments, "question", LONGEST_QUESTION))
        return {"ok": True, "answer": answer[:LONGEST_ANSWER], "from": client.name, "detail": f"{client.name} answered."}

    return [
        Tool(
            "ask_grok",
            "Send a question to the second AI for harder thinking, or when the owner asks for it. That is Grok "
            "from xAI, or Groq's free models if only Groq is connected. The question leaves this computer.",
            {"question": (STRING, "The full question, with no private details the owner did not ask to share.")},
            ("question",),
            run=ask,
        ),
    ]
