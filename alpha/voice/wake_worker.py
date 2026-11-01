"""Listens for "hey" or "hi" in its own process, so a crash here costs only this feature.

Sound arrives on standard input as 16 kHz, 16-bit mono. Events leave on standard
output as one JSON object per line. Nothing here touches the network.
"""

from __future__ import annotations

import json
import os
import sys
import time

WAKE_WORDS = ("hey", "hi")
DECISION_WORDS = ("confirm", "cancel")
KEYWORD_VARIABLE = "ALPHA_OWNER_KEYWORD"
READ_BYTES = 3_200
REPEAT_GAP_SECONDS = 2.0


def say(event: str) -> None:
    sys.stdout.write(json.dumps({"event": event}) + "\n")
    sys.stdout.flush()


def main(model_path: str) -> None:
    import vosk

    vosk.SetLogLevel(-1)
    model = vosk.Model(model_path)
    keyword = " ".join(os.environ.get(KEYWORD_VARIABLE, "").lower().split())
    known = bool(keyword) and all(model.vosk_model_find_word(word) >= 0 for word in keyword.split())
    phrases = [*WAKE_WORDS, *DECISION_WORDS, *([keyword] if known else []), "[unk]"]
    listener = vosk.KaldiRecognizer(model, 16_000, json.dumps(phrases))
    say("ready")
    if keyword and not known:
        say("keyword_unknown")
    last_wake = 0.0
    while True:
        sound = sys.stdin.buffer.read(READ_BYTES)
        if not sound:
            return
        if listener.AcceptWaveform(sound):
            heard = json.loads(listener.Result()).get("text", "")
        else:
            heard = json.loads(listener.PartialResult()).get("partial", "")
        words = heard.split()
        decision = next((word for word in words if word in DECISION_WORDS), None)
        if known and f" {keyword} " in f" {heard} ":
            decision = "keyword"
        if decision or any(word in WAKE_WORDS for word in words):
            listener.Reset()
            now = time.monotonic()
            if now - last_wake > REPEAT_GAP_SECONDS:
                last_wake = now
                say(decision or "wake")


if __name__ == "__main__":
    main(sys.argv[1])
