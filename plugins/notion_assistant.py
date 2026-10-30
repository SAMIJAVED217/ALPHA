"""
[ALPHA] Notion — search, read, create and append to pages.

Setup (once):
    1. https://www.notion.so/developers/tokens → New token → capability
       "Notion API" (read + insert + update content), expiry up to 1 year.
       Copy the ntn_… token.
    2. ⚙ SETUP → PLUGIN SETTINGS → NOTION: paste the token, and (optional)
       paste the link of the page new notes should go under. SAVE, then
       TEST CONNECTION.

A personal access token acts as you, so it sees every page you can see — no
"Add connections" step. The token is stored in config/api_keys.json (plain
text, like every other key there). Nothing is ever deleted by this plugin.
"""

import re

PLUGIN = {
    "name": "notion_assistant",
    "description": (
        "The user's Notion workspace (notes, study notes, to-dos, plans). "
        "action='search' finds pages whose title matches 'query'. "
        "action='read' returns a page's text ('page' = a page title, link or id). "
        "action='create' makes a new page with 'title' and 'content' under "
        "'parent' (a page title/link) or, if omitted, the user's default notes "
        "page. action='append' adds 'content' to the end of an existing 'page'. "
        "Content is plain text: lines starting '# ' become headings, '- ' "
        "bullets, '1. ' numbered items, '[ ] ' or '[x] ' to-dos. Use this when "
        "the user mentions Notion or asks to save/write a note to Notion. "
        "Deleting is not supported."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {"type": "STRING", "description": "search | read | create | append. Default search."},
            "query": {"type": "STRING", "description": "Words to search page titles for (action=search)."},
            "page": {"type": "STRING", "description": "Page title, Notion link or id (read / append)."},
            "title": {"type": "STRING", "description": "Title of the new page (create)."},
            "content": {"type": "STRING", "description": "Text to write (create / append)."},
            "parent": {"type": "STRING", "description": "Optional parent page title or link for create."},
        },
        "required": [],
    },
    "tier": "extra",
    "brief": "Notion: search, read, create and append pages.",
}

_NS = "notion_assistant"
_API = "https://api.notion.com/v1"
_VERSION = "2026-03-11"
_UUID_RE = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)


class NotionError(Exception):
    pass


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _token(values=None):
    if values and values.get("token"):
        return str(values["token"]).strip()
    from memory.config_manager import get_plugin_setting
    return str(get_plugin_setting(_NS, "token", "") or "").strip()


def _call(method, path, token, body=None, params=None):
    import requests
    if not token:
        raise NotionError("Notion isn't connected yet: paste your Notion token in "
                          "Plugin Settings → Notion.")
    r = requests.request(
        method, f"{_API}{path}", json=body, params=params, timeout=25,
        headers={"Authorization": f"Bearer {token}", "Notion-Version": _VERSION,
                 "Content-Type": "application/json"})
    if r.status_code == 401:
        raise NotionError("Notion rejected the token (wrong or expired). Make a new one "
                          "at notion.so/developers/tokens and paste it in Plugin Settings.")
    if r.status_code == 404:
        raise NotionError("That Notion page wasn't found, or the token has no access to it.")
    if r.status_code == 429:
        raise NotionError("Notion is rate-limiting requests; try again in a few seconds.")
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
        except Exception:
            msg = r.text
        raise NotionError(f"Notion error {r.status_code}: {str(msg)[:240]}")
    return r.json() if r.content else {}


# ── text helpers ──────────────────────────────────────────────────────────────

def _plain(rich):
    return "".join(t.get("plain_text", "") for t in (rich or []))


def _title_of(page):
    for prop in (page.get("properties") or {}).values():
        if prop.get("type") == "title":
            return _plain(prop.get("title")) or "Untitled"
    return "Untitled"


def _rich(text):
    text = str(text)
    return [{"type": "text", "text": {"content": text[i:i + 1900]}}
            for i in range(0, len(text), 1900)][:100] or [{"type": "text", "text": {"content": ""}}]


def _block(kind, text, **extra):
    data = {"rich_text": _rich(text)}
    data.update(extra)
    return {"object": "block", "type": kind, kind: data}


def text_to_blocks(content):
    """Plain text with light markdown → Notion blocks."""
    blocks, code, in_code = [], [], False
    for raw in str(content or "").replace("\r\n", "\n").split("\n"):
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                blocks.append(_block("code", "\n".join(code), language="plain text"))
                code, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code.append(raw)
            continue
        s = line.strip()
        if not s:
            continue
        m = re.match(r"^(#{1,3})\s+(.*)$", s)
        if m:
            level = {1: "heading_2", 2: "heading_3", 3: "heading_3"}[len(m.group(1))]
            blocks.append(_block(level, m.group(2)))
        elif re.match(r"^[-*•]\s+", s):
            blocks.append(_block("bulleted_list_item", re.sub(r"^[-*•]\s+", "", s)))
        elif re.match(r"^\d+[.)]\s+", s):
            blocks.append(_block("numbered_list_item", re.sub(r"^\d+[.)]\s+", "", s)))
        elif re.match(r"^\[( |x|X)\]\s*", s):
            done = s[1] in "xX"
            blocks.append(_block("to_do", re.sub(r"^\[( |x|X)\]\s*", "", s), checked=done))
        elif s.startswith("> "):
            blocks.append(_block("quote", s[2:]))
        else:
            blocks.append(_block("paragraph", s))
    if code:
        blocks.append(_block("code", "\n".join(code), language="plain text"))
    return blocks


_PREFIX = {"heading_1": "# ", "heading_2": "## ", "heading_3": "### ",
           "bulleted_list_item": "• ", "numbered_list_item": "- ", "quote": "> ",
           "callout": "! ", "toggle": "▸ "}


def _block_text(b):
    kind = b.get("type", "")
    data = b.get(kind, {}) or {}
    if kind == "child_page":
        return f"[sub-page] {data.get('title', '')}"
    if kind == "child_database":
        return f"[database] {data.get('title', '')}"
    if kind == "to_do":
        return ("[x] " if data.get("checked") else "[ ] ") + _plain(data.get("rich_text"))
    if kind == "code":
        return "```\n" + _plain(data.get("rich_text")) + "\n```"
    if kind == "divider":
        return "---"
    if "rich_text" in data:
        return _PREFIX.get(kind, "") + _plain(data.get("rich_text"))
    return ""


# ── page lookup ───────────────────────────────────────────────────────────────

def _search_pages(token, query, n=8):
    body = {"query": query or "", "page_size": n,
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"}}
    return _call("POST", "/search", token, body).get("results", [])


def _page_id(ref):
    """The page id inside a Notion link or id string, else None (it's a title)."""
    ref = str(ref or "").strip()
    m = _UUID_RE.search(ref)
    if m:
        return m.group(1).replace("-", "").lower()
    if re.fullmatch(r"[0-9a-fA-F]{32}", ref):
        return ref.lower()
    if "notion." in ref or ref.startswith("http"):
        q = re.search(r"[?&]p=([0-9a-fA-F]{32})", ref)      # side-peek links
        if q:
            return q.group(1).lower()
        last = ref.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
        # The id is the LAST 32 hex characters: "Deadline-1a2b…" must not
        # borrow the title's trailing "e".
        m = re.search(r"([0-9a-fA-F]{32})$", last)
        if m:
            return m.group(1).lower()
    return None


def _resolve(token, ref):
    """A page title, link or id → (page_id, title)."""
    pid = _page_id(ref)
    if pid:
        page = _call("GET", f"/pages/{pid}", token)
        return page["id"], _title_of(page)
    results = _search_pages(token, ref, n=10)
    if not results:
        raise NotionError(f"No Notion page titled like '{ref}'.")
    want = str(ref).strip().lower()
    exact = [p for p in results if _title_of(p).strip().lower() == want]
    page = (exact or results)[0]
    return page["id"], _title_of(page)


def _append_blocks(token, pid, blocks):
    for i in range(0, len(blocks), 100):
        _call("PATCH", f"/blocks/{pid}/children", token, {"children": blocks[i:i + 100]})


# ── actions ───────────────────────────────────────────────────────────────────

def _do_search(token, query):
    results = _search_pages(token, query)
    if not results:
        return f"No Notion pages match '{query}'." if query else "No Notion pages found."
    lines = [f"- {_title_of(p)} (edited {p.get('last_edited_time', '')[:10]}) id {p['id']}"
             for p in results]
    return f"Notion pages for '{query}':\n" + "\n".join(lines)


def _do_read(token, ref):
    pid, title = _resolve(token, ref)
    out, cursor, count = [], None, 0
    while count < 300:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        res = _call("GET", f"/blocks/{pid}/children", token, params=params)
        for b in res.get("results", []):
            count += 1
            t = _block_text(b)
            if t:
                out.append(t)
            if b.get("has_children") and b.get("type") in (
                    "toggle", "bulleted_list_item", "numbered_list_item", "to_do", "callout", "quote"):
                kids = _call("GET", f"/blocks/{b['id']}/children", token,
                             params={"page_size": 50}).get("results", [])
                out.extend("    " + _block_text(k) for k in kids if _block_text(k))
        if not res.get("has_more"):
            break
        cursor = res.get("next_cursor")
    text = "\n".join(out).strip() or "[this page is empty]"
    if len(text) > 6000:
        text = text[:6000] + "\n… [truncated]"
    return f"Notion page: {title}\n\n{text}"


def _do_create(token, p):
    title = str(p.get("title", "") or "").strip() or "Note from ALPHA"
    parent_ref = str(p.get("parent", "") or "").strip()
    if not parent_ref:
        from memory.config_manager import get_plugin_setting
        parent_ref = str(get_plugin_setting(_NS, "default_parent", "") or "").strip()
    if not parent_ref:
        raise NotionError("Where should the page go? Set a default notes page in Plugin "
                          "Settings → Notion, or tell me the parent page.")
    parent_id, parent_title = _resolve(token, parent_ref)
    blocks = text_to_blocks(p.get("content", ""))
    page = _call("POST", "/pages", token, {
        "parent": {"type": "page_id", "page_id": parent_id},
        "properties": {"title": {"title": _rich(title)}},
        "children": blocks[:100],
    })
    if len(blocks) > 100:
        _append_blocks(token, page["id"], blocks[100:])
    return f"Created the Notion page '{title}' inside '{parent_title}'."


def _do_append(token, p):
    ref = str(p.get("page", "") or "").strip()
    if not ref:
        return "Which Notion page should I add to?"
    blocks = text_to_blocks(p.get("content", ""))
    if not blocks:
        return "There is nothing to add."
    pid, title = _resolve(token, ref)
    _append_blocks(token, pid, blocks)
    return f"Added {len(blocks)} line(s) to the Notion page '{title}'."


def _test(values):
    try:
        token = _token(values)
        results = _search_pages(token, "", n=3)
        names = ", ".join(_title_of(p) for p in results) or "no pages yet"
        msg = f"Connected ✓ — recent pages: {names}"[:160]
        parent = str((values or {}).get("default_parent", "") or "").strip()
        if parent:
            _, ptitle = _resolve(token, parent)
            msg += f" · new notes go in '{ptitle}'"
        return True, msg
    except NotionError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Could not reach Notion: {e}"


PLUGIN_SETTINGS = {
    "title": "NOTION",
    "fields": [
        {"key": "token", "type": "password", "label": "Notion token (ntn_…)",
         "placeholder": "ntn_…"},
        {"key": "default_parent", "type": "text",
         "label": "Default page for new notes (link or title)",
         "placeholder": "https://www.notion.so/…"},
    ],
    "action": {"label": "TEST CONNECTION", "run": _test},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    p = parameters or {}
    action = str(p.get("action", "search") or "search").strip().lower()
    try:
        token = _token()
        if action == "read":
            out = _do_read(token, p.get("page") or p.get("query") or p.get("title"))
        elif action == "create":
            out = _do_create(token, p)
        elif action == "append":
            out = _do_append(token, p)
        else:
            out = _do_search(token, str(p.get("query", "") or p.get("page", "") or "").strip())
        if player:
            try:
                player.write_log(f"SYS: Notion — {action}.")
            except Exception:
                pass
        return out
    except NotionError as e:
        return str(e)
    except Exception as e:
        return f"Notion error: {str(e)[:300]}"
