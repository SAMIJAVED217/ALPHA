"""Asking Grok, through xAI's API with a key kept in the Credential Manager."""

from __future__ import annotations

import httpx

from .. import keystore
from .registry import STRING, Tool, ToolRefused, text

KEY_ACCOUNT = "xai-key"
API = "https://api.x.ai/v1/chat/completions"
MODEL = "grok-latest"
LONGEST_QUESTION = 8_000
LONGEST_ANSWER = 8_000


class GrokClient:
    def __init__(self, load_key=lambda: keystore.load_secret(KEY_ACCOUNT), transport=None):
        self._load_key = load_key
        self._transport = transport

    def ask(self, question: str) -> str:
        key = self._load_key()
        if not key:
            raise ToolRefused("Grok is not connected yet. Add an xAI key under Settings, Connections.")
        body = {"model": MODEL, "messages": [{"role": "user", "content": question}]}
        try:
            with httpx.Client(transport=self._transport, timeout=60) as client:
                response = client.post(API, headers={"Authorization": f"Bearer {key}"}, json=body)
        except httpx.HTTPError as error:
            raise ToolRefused(f"Grok could not be reached ({type(error).__name__}).") from error
        if response.status_code in (401, 403):
            raise ToolRefused("xAI refused the key. Check it under Settings, Connections.")
        if response.status_code >= 400:
            raise ToolRefused(f"xAI answered with error {response.status_code}.")
        try:
            return response.json()["choices"][0]["message"]["content"]
        except (ValueError, LookupError, TypeError) as error:
            raise ToolRefused("xAI sent back something unexpected.") from error


def grok_tools(client: GrokClient | None) -> list[Tool]:
    def ask(arguments):
        answer = client.ask(text(arguments, "question", LONGEST_QUESTION))
        return {"ok": True, "answer": answer[:LONGEST_ANSWER], "detail": "Grok answered."}

    return [
        Tool(
            "ask_grok",
            "Send a question to Grok, only when the owner asks for Grok. The question leaves this computer for xAI.",
            {"question": (STRING, "The question, with no private details the owner did not ask to share.")},
            ("question",),
            run=ask,
            reads_outside=True,
        ),
    ]
