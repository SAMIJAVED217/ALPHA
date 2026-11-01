"""Watches a web page or an open window for new lines that matter to the owner.

Text from a watched page goes to the owner and never to the model, so a page cannot
give APLHA instructions. Runs only on the tools thread. No password is ever kept: a
page that needs signing in is read from the owner's own open browser window.
"""

from __future__ import annotations

import hashlib
import html
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Callable

import httpx

from . import paths

log = logging.getLogger(__name__)

SHORTEST_LINE = 8
MOST_REMEMBERED = 5_000
_HIDDEN = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_BREAK = re.compile(r"<(?:br|/p|/div|/li|/tr|/td|/h[1-6])\b[^>]*>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_SIGN_IN = re.compile(r"<input\b[^>]*type=[\"']?password", re.IGNORECASE)


class WatchProblem(Exception):
    """The page could not be read. The message is shown to the owner."""


@dataclass
class Watch:
    name: str
    source: str
    looking_for: list[str]
    seen: list[str] = field(default_factory=list)
    started: bool = False
    problem: str = ""


@dataclass(frozen=True)
class Found:
    name: str
    lines: tuple[str, ...] = ()
    problem: str = ""


def lines_of_page(page: str) -> list[str]:
    text = _BREAK.sub("\n", _HIDDEN.sub(" ", page))
    plain = html.unescape(_TAG.sub(" ", text))
    return [" ".join(line.split()) for line in plain.splitlines() if line.strip()]


def fetch_page(address: str, transport=None) -> list[str]:
    try:
        with httpx.Client(transport=transport, timeout=20, follow_redirects=True) as client:
            response = client.get(address, headers={"User-Agent": "Mozilla/5.0 APLHA"})
    except httpx.HTTPError as error:
        raise WatchProblem(f"The page could not be reached ({type(error).__name__}).") from error
    if response.status_code >= 400:
        raise WatchProblem(f"The site answered with error {response.status_code}.")
    if _SIGN_IN.search(response.text):
        raise WatchProblem("This page needs signing in. Keep it open in your browser and watch that window instead.")
    return lines_of_page(response.text)


def _fingerprint(line: str) -> str:
    return hashlib.sha256(line.lower().encode("utf-8")).hexdigest()[:16]


def new_matches(watch: Watch, lines: list[str]) -> list[str]:
    """The first look only learns what is already there, so old notices do not raise alerts."""
    known = set(watch.seen)
    fresh = [line for line in dict.fromkeys(lines) if len(line) >= SHORTEST_LINE and _fingerprint(line) not in known]
    watch.seen = [*watch.seen, *map(_fingerprint, fresh)][-MOST_REMEMBERED:]
    if not watch.started:
        watch.started = True
        return []
    wanted = [word.lower() for word in watch.looking_for]
    return [line for line in fresh if any(word in line.lower() for word in wanted)]


class WatchBoard:
    def __init__(self, read_window: Callable[[str], list[str]], fetch: Callable[[str], list[str]] = fetch_page):
        self._read_window = read_window
        self._fetch = fetch
        self._watches: dict[str, Watch] | None = None

    @property
    def watches(self) -> dict[str, Watch]:
        if self._watches is None:
            self._watches = self._load()
        return self._watches

    @staticmethod
    def _file():
        return paths.data_dir() / "watches.json"

    def _load(self) -> dict[str, Watch]:
        try:
            saved = json.loads(self._file().read_text(encoding="utf-8"))
            return {item["name"]: Watch(**item) for item in saved}
        except FileNotFoundError:
            return {}
        except (ValueError, TypeError, KeyError):
            log.warning("The watch list could not be read, so it starts empty.")
            return {}

    def _save(self) -> None:
        temporary = self._file().with_suffix(".tmp")
        temporary.write_text(json.dumps([asdict(item) for item in self.watches.values()]), encoding="utf-8")
        temporary.replace(self._file())

    def add(self, name: str, source: str, looking_for: list[str]) -> Watch:
        self.watches[name] = Watch(name, source, looking_for)
        self._save()
        return self.watches[name]

    def remove(self, name: str) -> bool:
        found = self.watches.pop(name, None) is not None
        if found:
            self._save()
        return found

    def _lines_from(self, source: str) -> list[str]:
        return self._fetch(source) if source.lower().startswith(("http://", "https://")) else self._read_window(source)

    def check_all(self) -> list[Found]:
        """A problem is reported once, not at every check."""
        results = []
        for watch in self.watches.values():
            try:
                lines = self._lines_from(watch.source)
            except Exception as error:
                problem = str(error) or type(error).__name__
                if problem != watch.problem:
                    watch.problem = problem
                    results.append(Found(watch.name, problem=problem))
                continue
            watch.problem = ""
            matches = new_matches(watch, lines)
            if matches:
                results.append(Found(watch.name, tuple(matches)))
        if self.watches:
            self._save()
        return results
