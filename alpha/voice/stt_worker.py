"""Turns speech into text with Whisper in its own process, entirely on this computer.

Standard input carries frames: a 4-byte little-endian length, then that many
bytes of 16 kHz, 16-bit mono sound. A length of 0 ends the turn. Standard output
carries one JSON object per line.
"""

from __future__ import annotations

import json
import struct
import sys

THREADS = 4


def say(**event) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def main(model_path: str, language: str) -> None:
    import numpy
    from faster_whisper import WhisperModel

    model = WhisperModel(model_path, device="cpu", compute_type="int8", cpu_threads=THREADS)
    say(event="ready")
    sound = bytearray()
    while True:
        head = sys.stdin.buffer.read(4)
        if len(head) < 4:
            return
        size = struct.unpack("<I", head)[0]
        if size:
            sound.extend(sys.stdin.buffer.read(size))
            continue
        samples = numpy.frombuffer(bytes(sound), dtype="<i2").astype(numpy.float32) / 32768.0
        sound.clear()
        # Naming the language skips detection, which takes about half the time.
        segments, _ = model.transcribe(
            samples,
            beam_size=1,
            vad_filter=True,
            condition_on_previous_text=False,
            language=None if language == "auto" else language,
        )
        say(event="text", text=" ".join(part.text.strip() for part in segments).strip())


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
