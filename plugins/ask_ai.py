"""
[ALPHA] Ask AI — free large models for careful, detailed answers.

Providers, tried in this order (only the ones you gave a key for):
    1. Groq        — free plan, very fast. Default model: openai/gpt-oss-120b.
    2. OpenRouter  — backup; uses whichever ':free' model it currently offers
                     (free models: about 50 requests a day without credit).

Setup (once):
    Groq:       https://console.groq.com/keys → Create API Key → copy gsk_…
    OpenRouter: https://openrouter.ai/settings/keys → Create key → copy sk-or-…
    ⚙ SETUP → PLUGIN SETTINGS → ASK AI: paste, SAVE, then TEST KEYS.

TEST KEYS also makes Groq ALPHA's backup for background jobs (reading
documents, screen questions, code) when Gemini's free text quota runs out —
switch "Also use Groq as ALPHA's backup" off to skip that. You can undo it any
time in ⚙ → ENGINE & PROVIDERS ("Gemini only").

The full answer appears in the panel under the HUD; ALPHA speaks a short
summary. The last few exchanges are kept for 30 minutes for follow-ups.
"""

import re
import threading
import time

PLUGIN = {
    "name": "ask_ai",
    "description": (
        "Ask a large free AI model (GPT-OSS 120B on Groq, with OpenRouter as "
        "backup) for a careful, detailed answer. Use when the user says 'ask the "
        "AI', 'ask Groq', 'think harder', 'explain in detail' or 'step by step', "
        "or wants a derivation, worked example, proof, long write-up or "
        "non-trivial code that needs more reasoning than a quick spoken reply. "
        "Put the full request in 'prompt' and any material it needs (notes, an "
        "email's text, an error message) in 'context'. The full answer appears "
        "on the HUD panel: when it comes back, say a one- or two-sentence "
        "summary in the user's language — do not read it all aloud. Follow-ups "
        "like 'go deeper on that' continue the same conversation."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "prompt": {"type": "STRING", "description": "The user's request, in full."},
            "context": {"type": "STRING", "description": "Optional material needed to answer."},
            "new_topic": {"type": "BOOLEAN",
                          "description": "True to start fresh instead of continuing the last conversation."},
        },
        "required": ["prompt"],
    },
    "behavior": "NON_BLOCKING",
    "scheduling": "WHEN_IDLE",
    "tier": "extra",
    "brief": "Ask a big free model (Groq / OpenRouter) for a detailed answer.",
}

_NS = "ask_ai"
GROQ = "https://api.groq.com/openai/v1"
OPENROUTER = "https://openrouter.ai/api/v1"
AUTO = "auto (best available)"
GROQ_MODELS = [AUTO, "openai/gpt-oss-120b", "llama-3.3-70b-versatile", "openai/gpt-oss-20b",
               "llama-3.1-8b-instant"]
# Preference when picking automatically from what the server lists today.
_GROQ_PREFER = ("openai/gpt-oss-120b", "llama-3.3-70b-versatile", "qwen", "openai/gpt-oss-20b",
                "llama")
_OR_PREFER = ("deepseek", "gpt-oss", "qwen", "nemotron", "llama", "gemma", "mistral")
_NOT_CHAT = ("whisper", "guard", "orpheus", "tts", "embed", "safeguard")
_DEFAULT_ABOUT = "Mechatronics engineering student in Korea."
_HISTORY_TURNS = 3
_HISTORY_TTL = 30 * 60

_history: list = []
_last_at = 0.0
_model_cache: dict = {}        # provider -> model id picked automatically
_lock = threading.Lock()


class AIError(Exception):
    pass


class RateLimited(AIError):
    pass


def _setting(key, default=None, values=None):
    if values is not None and values.get(key) not in (None, ""):
        return values.get(key)
    from memory.config_manager import get_plugin_setting
    v = get_plugin_setting(_NS, key, default)
    return default if v in (None, "") else v


def _system(about):
    return (
        "You are a careful expert assistant answering a request relayed by ALPHA, "
        "the user's desktop voice assistant. "
        + (f"About the user: {about.strip()} " if about and about.strip() else "")
        + "Begin with a one- or two-sentence direct answer that can be read aloud, "
        "then give the full explanation with worked steps where useful. Your reply "
        "is shown in a small plain-text panel: use short paragraphs and simple '-' "
        "lists; avoid tables, Markdown headings and LaTeX. Say plainly when you are "
        "unsure or when facts may be out of date."
    )


# ── HTTP ──────────────────────────────────────────────────────────────────────

def _headers(key, provider):
    h = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if provider == "openrouter":
        h["X-Title"] = "ALPHA desktop assistant"
    return h


def _err(r, provider):
    try:
        msg = str((r.json().get("error") or {}).get("message") or r.text)
    except Exception:
        msg = r.text
    msg = msg[:240]
    name = "Groq" if provider == "groq" else "OpenRouter"
    if r.status_code in (401, 403):
        return AIError(f"{name} rejected the API key — check it in Plugin Settings → Ask AI.")
    if r.status_code == 429:
        return RateLimited(f"{name}'s free limit is used up for now.")
    if r.status_code in (404,) or "model" in msg.lower() and ("not found" in msg.lower()
                                                              or "does not exist" in msg.lower()
                                                              or "decommission" in msg.lower()):
        return AIError(f"MODEL_GONE {name}: {msg}")
    if r.status_code == 413 or "context" in msg.lower() and "length" in msg.lower():
        return AIError(f"That was too long for {name}'s model; try a shorter request.")
    if r.status_code >= 500:
        return RateLimited(f"{name} is having trouble right now ({r.status_code}).")
    return AIError(f"{name} error {r.status_code}: {msg}")


def _list_models(provider, key):
    import requests
    if provider == "openrouter":
        r = requests.get(f"{OPENROUTER}/models", timeout=20)
        if r.status_code >= 400:
            raise _err(r, provider)
        return [m.get("id", "") for m in r.json().get("data", [])
                if str(m.get("id", "")).endswith(":free")]
    r = requests.get(f"{GROQ}/models", headers=_headers(key, provider), timeout=20)
    if r.status_code >= 400:
        raise _err(r, provider)
    return [m.get("id", "") for m in r.json().get("data", [])
            if m.get("active", True) and not any(x in m.get("id", "").lower() for x in _NOT_CHAT)]


def _pick(ids, prefer):
    for want in prefer:
        for i in ids:
            if i == want:
                return i
        for i in ids:
            if want in i.lower():
                return i
    return ids[0] if ids else ""


def _model_for(provider, key, values=None, refresh=False):
    if provider == "groq":
        chosen = str(_setting("groq_model", AUTO, values))
        if chosen and chosen != AUTO and not refresh:
            return chosen
    with _lock:
        if not refresh and _model_cache.get(provider):
            return _model_cache[provider]
    ids = _list_models(provider, key)
    m = _pick(ids, _GROQ_PREFER if provider == "groq" else _OR_PREFER)
    if not m:
        raise AIError("OpenRouter has no free models right now." if provider == "openrouter"
                      else "Groq listed no chat models for this key.")
    with _lock:
        _model_cache[provider] = m
    return m


def _clean(text):
    # Some reasoning models put their thinking inline.
    text = re.sub(r"(?is)<think>.*?</think>", "", text or "")
    return text.strip()


def _chat(provider, key, model, messages, max_tokens, timeout=120):
    import requests
    base = GROQ if provider == "groq" else OPENROUTER
    body = {"model": model, "messages": messages}
    body["max_completion_tokens" if provider == "groq" else "max_tokens"] = int(max_tokens)
    r = requests.post(f"{base}/chat/completions", headers=_headers(key, provider),
                      json=body, timeout=timeout)
    if r.status_code >= 400:
        raise _err(r, provider)
    data = r.json()
    choice = (data.get("choices") or [{}])[0]
    text = _clean((choice.get("message") or {}).get("content") or "")
    return text, choice.get("finish_reason"), data.get("usage") or {}


def _providers(values=None):
    out = []
    g = str(_setting("groq_key", "", values) or "").strip()
    o = str(_setting("openrouter_key", "", values) or "").strip()
    if g:
        out.append(("groq", g))
    if o:
        out.append(("openrouter", o))
    return out


def _ask(messages, max_tokens, values=None):
    """Try each provider in turn. Returns (text, provider, model, finish, usage)."""
    provs = _providers(values)
    if not provs:
        raise AIError("No free AI key yet: paste a Groq key (and optionally an OpenRouter "
                      "key) in Plugin Settings → Ask AI.")
    notes = []
    for provider, key in provs:
        for attempt in (0, 1):
            try:
                model = _model_for(provider, key, values, refresh=attempt == 1)
                text, finish, usage = _chat(provider, key, model, messages, max_tokens)
                return text, provider, model, finish, usage
            except RateLimited as e:
                notes.append(str(e))
                break
            except AIError as e:
                if str(e).startswith("MODEL_GONE") and attempt == 0:
                    with _lock:
                        _model_cache.pop(provider, None)
                    continue
                if str(e).startswith("MODEL_GONE"):
                    notes.append(str(e).replace("MODEL_GONE ", ""))
                    break
                if len(provs) > 1:
                    notes.append(str(e))
                    break
                raise
    raise AIError(" ".join(notes) or "No provider answered.")


# ── backup provider for the rest of ALPHA ─────────────────────────────────────

def _use_as_backup(key, model):
    """Point ALPHA's background requests at Groq when Gemini cannot answer.
    Returns a short note for the status line."""
    try:
        from memory.config_manager import (get_pipeline, get_provider_config,
                                           get_provider_mode, get_sidecar_provider,
                                           save_providers)
        cur = get_provider_config("openai_compat")
        side = get_sidecar_provider()
        busy = (get_provider_mode() == "pipeline" and get_pipeline().get("llm") == "openai_compat"
                and cur.get("catalog") not in ("", None, "groq"))
        if busy or (side and side != "openai_compat") or \
                (side == "openai_compat" and cur.get("catalog") not in ("", None, "groq")):
            return " · backup not changed (another provider is set in Engine & Providers)"
        save_providers(creds={"openai_compat": {"catalog": "groq", "base_url": GROQ,
                                                "model": model, "api_key": key}},
                       sidecar="openai_compat")
        return " · Groq is now ALPHA's backup"
    except Exception as e:
        return f" · could not set backup ({e})"


def _test(values):
    provs = _providers(values)
    if not provs:
        return False, "Paste a Groq key first (OpenRouter is optional)."
    parts, ok_any, groq_ok = [], False, None
    for provider, key in provs:
        name = "Groq" if provider == "groq" else "OpenRouter"
        try:
            model = _model_for(provider, key, values, refresh=True)
            text, _f, _u = _chat(provider, key, model,
                                 [{"role": "user", "content": "Reply with the word OK."}], 300,
                                 timeout=40)
            parts.append(f"{name} ✓ {model}")
            ok_any = True
            if provider == "groq":
                groq_ok = (key, model)
        except AIError as e:
            parts.append(f"{name} ✗ {str(e).replace('MODEL_GONE ', '')}")
        except Exception as e:
            parts.append(f"{name} ✗ {e}")
    msg = " · ".join(parts)
    backup = _setting("use_as_backup", True, values)
    if groq_ok and backup is not False:
        msg += _use_as_backup(*groq_ok)
    return ok_any, msg[:300]


PLUGIN_SETTINGS = {
    "title": "ASK AI  (free)",
    "fields": [
        {"key": "groq_key", "type": "password", "label": "Groq API key (gsk_…)",
         "placeholder": "gsk_…"},
        {"key": "groq_model", "type": "choice", "label": "Groq model",
         "options": GROQ_MODELS, "default": AUTO},
        {"key": "openrouter_key", "type": "password",
         "label": "OpenRouter API key (sk-or-…) — optional backup",
         "placeholder": "sk-or-…"},
        {"key": "max_tokens", "type": "choice", "label": "Longest answer (tokens)",
         "options": ["2048", "4096", "8192"], "default": "4096"},
        {"key": "about", "type": "text", "label": "About you (sent with each question)",
         "default": _DEFAULT_ABOUT},
        {"key": "use_as_backup", "type": "toggle",
         "label": "Also use Groq as ALPHA's backup when Gemini's free quota runs out",
         "default": True},
    ],
    "action": {"label": "TEST KEYS", "run": _test},
}


def run(parameters: dict, player=None, session_memory=None) -> str:
    global _history, _last_at
    p = parameters or {}
    prompt = str(p.get("prompt", "") or "").strip()
    if not prompt:
        return "What should I ask?"
    context = str(p.get("context", "") or "").strip()
    user_msg = prompt if not context else f"{prompt}\n\n--- Context ---\n{context[:20000]}"
    try:
        max_tokens = int(_setting("max_tokens", "4096"))
    except (TypeError, ValueError):
        max_tokens = 4096

    with _lock:
        if p.get("new_topic") or time.time() - _last_at > _HISTORY_TTL:
            _history = []
        history = list(_history[-2 * _HISTORY_TURNS:])
    messages = ([{"role": "system", "content": _system(str(_setting("about", _DEFAULT_ABOUT)))}]
                + history + [{"role": "user", "content": user_msg}])

    if player:
        try:
            player.write_log("SYS: Asking the free AI model…")
        except Exception:
            pass
    try:
        text, provider, model, finish, usage = _ask(messages, max_tokens)
    except AIError as e:
        return str(e)
    except Exception as e:
        return f"Couldn't reach the AI service: {str(e)[:200]}"

    answer = text or "[The model returned no text — try again or ask more simply.]"
    if finish == "length":
        answer += "\n\n[Cut off at the length limit — raise it in Plugin Settings → Ask AI.]"

    with _lock:
        _history = (history + [{"role": "user", "content": user_msg},
                               {"role": "assistant", "content": answer}])[-2 * _HISTORY_TURNS:]
        _last_at = time.time()

    who = ("Groq" if provider == "groq" else "OpenRouter") + " · " + model.split("/")[-1]
    if player:
        try:
            player.show_content(f"AI · {who}"[:48], answer)
            player.write_log(f"SYS: {who} answered ({usage.get('prompt_tokens', '?')} in / "
                             f"{usage.get('completion_tokens', '?')} out tokens).")
        except Exception:
            pass

    spoken = answer if len(answer) <= 3000 else answer[:3000] + " …"
    return ("[The full answer is now on the HUD panel. Give the user a one- or "
            "two-sentence summary in their language; do not read it all.]\n\n" + spoken)
