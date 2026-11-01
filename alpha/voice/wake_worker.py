"""Listens for "hey" or "hi" in its own process, so a crash here costs only this feature.

Sound arrives on standard input as 16 kHz, 16-bit mono. Events leave on standard
output as one JSON object per line. Nothing here touches the network.
"""

from __future__ import annotations

import json
import sys
import time

WAKE_WORDS = ("hey", "hi")
READ_BYTES = 3_200
REPEAT_GAP_SECONDS = 2.0


def say(event: str) -> None:
    sys.stdout.write(json.dumps({"event": event}) + "\n")
    sys.stdout.flush()


def main(model_path: str) -> None:
    import vosk

    vosk.SetLogLevel(-1)
    model = vosk.Model(model_path)
    listener = vosk.KaldiRecognizer(model, 16_000, json.dumps([*WAKE_WORDS, "[unk]"]))
    say("ready")
    last_wake = 0.0
    while True:
        sound = sys.stdin.buffer.read(READ_BYTES)
        if not sound:
            return
        if listener.AcceptWaveform(sound):
            heard = json.loads(listener.Result()).get("text", "")
        else:
            heard = json.loads(listener.PartialResult()).get("partial", "")
        if any(word in WAKE_WORDS for word in heard.split()):
            listener.Reset()
            now = time.monotonic()
            if now - last_wake > REPEAT_GAP_SECONDS:
                last_wake = now
                say("wake")


if __name__ == "__main__":
    main(sys.argv[1])
