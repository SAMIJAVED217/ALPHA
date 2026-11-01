"""Notion, through an internal integration token kept in the Credential Manager."""

from __future__ import annotations

import re

import httpx

from .. import keystore
from .registry import STRING, Plan, Tool, ToolRefused, text

TOKEN_ACCOUNT = "notion-token"
API = "https://api.notion.com/v1"
API_VERSION = "2022-06-28"
_PAGE_ID = re.compile(r"^[0-9a-fA-F-]{32,36}$")
MOST_RESULTS = 10
MOST_BLOCKS = 100


def _page_id(arguments: dict) -> str:
    value = text(arguments, "page_id", 40)
    if not _PAGE_ID.match(value):
        raise ToolRefused("That is not a Notion page id.")
    return value


def _plain(rich: list) -> str:
    return "".join(piece.get("plain_text", "") for piece in rich)


def _title_of(page: dict) -> str:
    for value in page.get("properties", {}).values():
        if value.get("type") == "title":
            return _plain(value.get("title", [])) or "Untitled"
    return "Untitled"


def _paragraph(content: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:2000]}}]},
    }


class NotionClient:
    def __init__(self, load_token=lambda: keystore.load_secret(TOKEN_ACCOUNT), transport=None):
        self._load_token = load_token
        self._transport = transport

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        token = self._load_token()
        if not token:
            raise ToolRefused("Notion is not connected yet. Add a token under Settings, Connections.")
        headers = {"Authorization": f"Bearer {token}", "Notion-Version": API_VERSION}
        with httpx.Client(transport=self._transport, timeout=20) as client:
            response = client.request(method, f"{API}{path}", headers=headers, json=body)
        if response.status_code in (401, 403):
            raise ToolRefused("Notion refused access. Check the token, and share the page with the integration.")
        if response.status_code == 404:
            raise ToolRefused("Notion could not find that. The page may not be shared with the integration.")
        if response.status_code >= 400:
            raise ToolRefused(f"Notion answered with an error ({response.status_code}).")
        return response.json()

    def check(self) -> None:
        """Proves the token works. Pages still have to be shared with the integration."""
        self.request("GET", "/users/me")


def notion_tools(client: NotionClient) -> list[Tool]:
    def search(arguments):
        found = client.request(
            "POST", "/search", {"query": text(arguments, "query", 200), "page_size": MOST_RESULTS,
                                "filter": {"property": "object", "value": "page"}},
        )
        pages = [{"page_id": page["id"], "title": _title_of(page)} for page in found.get("results", [])]
        return {"ok": True, "pages": pages, "detail": f"Found {len(pages)} Notion pages."}

    def read(arguments):
        page_id = _page_id(arguments)
        found = client.request("GET", f"/blocks/{page_id}/children?page_size={MOST_BLOCKS}")
        lines = []
        for block in found.get("results", []):
            body = block.get(block.get("type", ""), {})
            if isinstance(body, dict) and "rich_text" in body:
                lines.append(_plain(body["rich_text"]))
        return {"ok": True, "untrusted_page_text": "\n".join(lines), "detail": f"Read {len(lines)} lines from Notion."}

    def plan_add(arguments):
        page_id = _page_id(arguments)
        content = text(arguments, "text", 2_000)

        def perform():
            client.request("PATCH", f"/blocks/{page_id}/children", {"children": [_paragraph(content)]})
            return "Added the text to the Notion page."

        return Plan("add text to a Notion page", f"Page {page_id}", content, perform)

    def plan_create(arguments):
        parent = _page_id(arguments)
        title = text(arguments, "title", 200)
        content = text(arguments, "text", 2_000)

        def perform():
            made = client.request(
                "POST",
                "/pages",
                {
                    "parent": {"page_id": parent},
                    "properties": {"title": {"title": [{"text": {"content": title}}]}},
                    "children": [_paragraph(content)],
                },
            )
            return f"Created the Notion page '{title}' ({made.get('id', 'no id returned')})."

        return Plan(f"create a Notion page: {title}", f"Inside page {parent}", content, perform)

    page = {"page_id": (STRING, "The page id, from notion_search.")}
    return [
        Tool("notion_search", "Search the owner's shared Notion pages by title.",
             {"query": (STRING, "Words to look for.")}, ("query",), run=search),
        Tool("notion_read", "Read the text of a Notion page.", page, ("page_id",), run=read),
        Tool("propose_notion_add", "Add text to a Notion page.",
             {**page, "text": (STRING, "The text to add.")}, ("page_id", "text"), propose=plan_add),
        Tool("propose_notion_create", "Create a Notion page inside another page.",
             {**page, "title": (STRING, "The new page's title."), "text": (STRING, "Its text.")},
             ("page_id", "title", "text"), propose=plan_create),
    ]
