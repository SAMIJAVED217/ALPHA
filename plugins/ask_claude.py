"""
[ALPHA] Ask Claude — hand a question or task to Claude (Anthropic) and show the answer.

Setup (once):
    1. https://platform.claude.com → Settings → API keys → Create key.
       The API is billed separately from a Claude Pro subscription; Max plans
       include monthly API credits once a Console org is linked.
    2. ⚙ SETUP → PLUGIN SETTINGS → CLAUDE: paste the key, pick a model, SAVE,
       then TEST KEY.

Only runs when you explicitly ask for Claude ("ask Claude …"), because every
request costs money. The full answer appears in the panel under the HUD; ALPHA
speaks a short summary. The last few exchanges are kept for 30 minutes so you
can say "ask Claude to go deeper on that".
"""

import threading
import time

PLUGIN = {
    "name": "ask_claude",
    "description": (
        "Send a question or task to Claude, Anthropic's AI, and get its answer. "
        "Use ONLY when the user explicitly asks for Claude (e.g. 'ask Claude…', "
        "'have Claude explain…', 'use Claude to write…') — each call costs the "
        "user money. Put the user's full request in 'prompt' and any material "
        "Claude needs (an email's text, notes, an error message) in 'context'. "
        "Claude's full answer appears on the HUD panel: when the result comes "
        "back, say a one- or two-sentence summary in the user's language — do "
        "not read the whole answer aloud. Follow-ups like 'ask Claude to go "
        "deeper' continue the same conversation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "The user's request for Claude, in full."},
            "context": {"type": "STRING", "description": "Optional material Claude needs to answer."},
            "new_topic": {"type": "BOOLEAN",
                          "description": "True to start fresh instead of continuing the last Claude conversation."},
        },
        "required": ["prompt"],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "WHEN_IDLE",
    "tier": "extra",
    "brief": "Ask Claude (Anthropic) — only when the user asks for Claude by name.",
}

_NS = "ask_claude"
_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2026-10-21"
MODELS = ["claude-sonnet-5-5", "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"]
_DEFAULT_MODEL = "claude-sonnet-5-5"
_DEFAULT_ABOUT = "Mechatronics engineering student in Korea."
_HISTORY_TURNS = 3          # question/answer pairs kept for follow-ups
_HISTORY_TTL = 30 * 60      # seconds before a follow-up starts fresh

_history: list = []          # [{"role": ..., "content": ...}, ...]
_last_at = 0.0
_lock = threading.Lock()


class ClaudeError(Exception):
    pass


def _setting(key, default=None, values=None):
    if values is not None and values.get(key) not in (None, ""):
        return values.get(key)
    from memory.config_manager import get_plugin_setting
    v = get_plugin_setting(_NS, key, default)
    return default if v in (None, "") else v


def _system(about):
    return (
        "You are Claude, answering a request relayed by ALPHA, the user's desktop "
        "voice assistant. "
        + (f"About the user: {about.strip()} " if about and about.strip() else "")
        + "Begin with a one- or two-sentence direct answer that can be read aloud. "
        "Then give the full explanation. Your reply is shown in a small plain-text "
        "panel, so use short paragraphs and simple '-' lists; avoid tables, "
        "Markdown headings and LaTeX. Be accurate, and say plainly when you are "
        "unsure or when something may have changed after your training."
    )


def _post(api_key, model, max_tokens, system, messages, timeout=120):
    import requests
    if not api_key:
        raise ClaudeError("Claude isn't connected yet: paste your Anthropic API key in "
                          "Plugin Settings → Claude.")
    r = requests.post(
        _URL, timeout=timeout,
        headers={"x-api-key": api_key, "anthropic-version": _API_VERSION,
                 "content-type": "application/json"},
        json={"model": model, "max_tokens": int(max_tokens), "system": system,
              "messages": messages})
    try:
        data = r.json()
    except Exception:
        data = {}
    if r.status_code >= 400:
        err = (data.get("error") or {}) if isinstance(data, dict) else {}
        msg = str(err.get("message") or r.text)[:240]
        kind = err.get("type", "")
        if r.status_code == 401 or kind == "authentication_error":
            raise ClaudeError("Anthropic rejected the API key. Check it in Plugin Settings → Claude.")
        if "credit" in msg.lower() and "low" in msg.lower():
            raise ClaudeError("The Anthropic account is out of API credit. Add credit at "
                              "platform.claude.com → Billing.")
        if r.status_code == 404 or kind == "not_found_error":
            raise ClaudeError(f"Model '{model}' isn't available to this key. Pick another "
                              f"model in Plugin Settings → Claude.")
        if r.status_code in (429, 529) or kind in ("rate_limit_error", "overloaded_error"):
            raise ClaudeError("Claude is busy right now (rate limit or overload). Try again shortly.")
        raise ClaudeError(f"Claude API error {r.status_code}: {msg}")
    return data


def _text_of(data):
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    return "\n".join(p for p in parts if p).strip()


def _test(values):
    try:
        model = _setting("model", _DEFAULT_MODEL, values)
        data = _post(_setting("api_key", "", values), model, 16,
                     "Reply with exactly: OK", [{"role": "user", "content": "Test"}], timeout=30)
        return True, f"Key works ✓ — {model} replied: {_text_of(data)[:30] or '(empty)'}"
    except ClaudeError as e:
        return False, str(e)
    except Exception as e:
        return False, f"Could not reach Anthropic: {e}"


PLUGIN_SETTINGS = {
    "title": "CLAUDE",
    "fields": [
        {"key": "api_key", "type": "password", "label": "Anthropic API key (sk-ant-…)",
         "placeholder": "sk-ant-…"},
        {"key": "model", "type": "choice", "label": "Model", "options": MODELS,
         "default": _DEFAULT_MODEL},
        {"key": "max_tokens", "type": "choice", "label": "Longest answer (tokens)",
         "options": ["1024", "2048", "4096", "8192"], "default": "2048"},
        {"key": "about", "type": "text", "label": "About you (sent with each question)",
         "default": _DEFAULT_ABOUT},
    ],
    "action": {"label": "TEST KEY", "run": _test},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _history, _last_at
    p = parameters or {}
    prompt = str(p.get("prompt", "") or "").strip()
    if not prompt:
        return "What should I ask Claude?"
    context = str(p.get("context", "") or "").strip()
    user_msg = prompt if not context else f"{prompt}\n\n--- Context ---\n{context[:20000]}"

    model = str(_setting("model", _DEFAULT_MODEL))
    try:
        max_tokens = int(_setting("max_tokens", "2048"))
    except (TypeError, ValueError):
        max_tokens = 2048

    with _lock:
        if p.get("new_topic") or time.time() - _last_at > _HISTORY_TTL:
            _history = []
        messages = _history[-2 * _HISTORY_TURNS:] + [{"role": "user", "content": user_msg}]

    if player:
        try:
            player.write_log(f"SYS: Asking Claude ({model})…")
        except Exception:
            pass
    try:
        data = _post(str(_setting("api_key", "")), model, max_tokens,
                     _system(str(_setting("about", _DEFAULT_ABOUT))), messages)
    except ClaudeError as e:
        return str(e)
    except Exception as e:
        return f"Couldn't reach Claude: {str(e)[:200]}"

    answer = _text_of(data) or "[Claude returned no text]"
    if data.get("stop_reason") == "max_tokens":
        answer += "\n\n[Answer cut off at the length limit — raise it in Plugin Settings → Claude.]"

    with _lock:
        _history = (messages + [{"role": "assistant", "content": answer}])[-2 * _HISTORY_TURNS:]
        _last_at = time.time()

    usage = data.get("usage") or {}
    if player:
        try:
            player.show_content(f"CLAUDE · {model}", answer)
            player.write_log(f"SYS: Claude answered ({usage.get('input_tokens', '?')} in / "
                             f"{usage.get('output_tokens', '?')} out tokens).")
        except Exception:
            pass

    spoken = answer if len(answer) <= 3000 else answer[:3000] + " …"
    return ("[Claude's answer is now on the HUD panel. Give the user a one- or "
            "two-sentence summary in their language; do not read it all.]\n\n" + spoken)
