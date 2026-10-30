"""
core/identity.py — the product's name, in one place.

Change NAME here and the window title, the header and the default assistant
name follow.

WAKE_PHRASE IS NOT THE PRODUCT NAME, AND THAT IS DELIBERATE.
    openwakeword ships pretrained models, and the only one close to this project
    is "hey_jarvis". Saying "Hey ALPHA" on screen while the detector listens for
    something else would be a promise the code does not keep, so WAKE_PHRASE
    names what is really listened for. It changes when a model for
    WANTED_WAKE_PHRASE exists and core/wake_word.py loads it.
"""
from __future__ import annotations

NAME = "ALPHA"
TAGLINE = "Your Personal AI Companion"
VERSION = "0.1.0"

WANTED_WAKE_PHRASE = "Hey ALPHA"
WAKE_PHRASE = "Hey Jarvis"            # what core/wake_word.py can detect today
