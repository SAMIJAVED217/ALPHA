"""Speaks text with a voice built into Windows, in its own process, entirely on this computer.

Standard input carries one JSON object per line, {"text": ...}. Standard output
carries frames: a 4-byte little-endian length, then 24 kHz, 16-bit mono sound.
The sound is returned, not played, so ALPHA's speaker and HUD treat it like any reply.
"""

from __future__ import annotations

import json
import struct
import sys

FORMAT_24KHZ_16BIT_MONO = 26
SPEAK_PLAIN_TEXT = 16
ENGLISH_US = "409"


def english_voice(voice):
    """Windows may default to another language, so an English voice is chosen when there is one."""
    voices = voice.GetVoices()
    english = [voices.Item(i) for i in range(voices.Count) if ENGLISH_US in voices.Item(i).GetAttribute("Language")]
    return english[0] if english else None


def main() -> None:
    import comtypes.client

    voice = comtypes.client.CreateObject("SAPI.SpVoice")
    voices = voice.GetVoices()
    chosen = english_voice(voice)
    if chosen is not None:
        voice.Voice = chosen
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return
        request = json.loads(line)
        for index in range(voices.Count):
            if request.get("voice", "?") in voices.Item(index).GetDescription():
                voice.Voice = voices.Item(index)
        stream = comtypes.client.CreateObject("SAPI.SpMemoryStream")
        stream.Format.Type = FORMAT_24KHZ_16BIT_MONO
        voice.AudioOutputStream = stream
        # Plain text only, so reply text can never act as speech-engine markup.
        voice.Speak(request["text"], SPEAK_PLAIN_TEXT)
        sound = bytes(stream.GetData())
        sys.stdout.buffer.write(struct.pack("<I", len(sound)) + sound)
        sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
