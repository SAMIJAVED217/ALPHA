"""Runs the real app against the local stand-in for Gemini.

Uses a throwaway data folder and an in-memory key, so it never touches real
settings or the Credential Manager. Real speakers and the real sound library
are used, and the microphone still opens only while the talk control is held.

    .venv\\Scripts\\python scripts\\dev_run.py [--expiry SECONDS] [--no-key] [--first-run]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

parser = argparse.ArgumentParser()
parser.add_argument("--expiry", type=float, default=60.0)
parser.add_argument("--no-key", action="store_true")
parser.add_argument("--first-run", action="store_true")
parser.add_argument("--home", default="")
parser.add_argument("--reply-delay", type=float, default=0.0)
parser.add_argument("--reply-seconds", type=float, default=0.7)
options = parser.parse_args()

os.environ["ALPHA_HOME"] = options.home or tempfile.mkdtemp(prefix="alpha-dev-")

import keyring  # noqa: E402
from keyring.backend import KeyringBackend  # noqa: E402


class ThrowawayKeyring(KeyringBackend):
    priority = 1
    saved: dict = {}

    def get_password(self, service, username):
        return self.saved.get((service, username))

    def set_password(self, service, username, password):
        self.saved[(service, username)] = password

    def delete_password(self, service, username):
        self.saved.pop((service, username), None)


keyring.set_keyring(ThrowawayKeyring())

from alpha import app, keystore, settings as settings_store  # noqa: E402
from alpha.actions import ActionGate  # noqa: E402
from alpha.voice import gemini  # noqa: E402
from tests.fake_live_server import GOOD_KEY, FakeLiveServer, point_sdk_at  # noqa: E402

server = FakeLiveServer(options.reply_delay, options.reply_seconds)
server.start()

if not options.no_key:
    keystore.save_key(GOOD_KEY)
if not options.first_run and not settings_store.load().setup_complete:
    settings_store.save(settings_store.Settings(setup_complete=True))


async def connect(api_key: str, model: str):
    point_sdk_at(server.port)
    return await gemini.connect(api_key, model)


print(f"ALPHA_HOME={os.environ['ALPHA_HOME']}", flush=True)
title = "ALPHA - TEST WINDOW - pretend Gemini - do not enter a real key"
sys.exit(app.run(connector=connect, gate=ActionGate(expiry_seconds=options.expiry), title=title))
