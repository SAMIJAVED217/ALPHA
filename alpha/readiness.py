"""Checks that libraries are installed by looking at files on disk.

Nothing here imports the libraries, so it is safe from any thread and costs
almost nothing, however heavy the library is.
"""

from __future__ import annotations

from importlib import metadata

REQUIRED = ("PySide6-Essentials", "google-genai", "sounddevice", "numpy", "keyring", "vosk", "faster-whisper")


def is_installed(distribution: str) -> bool:
    try:
        metadata.distribution(distribution)
    except metadata.PackageNotFoundError:
        return False
    return True


def missing() -> list[str]:
    return [name for name in REQUIRED if not is_installed(name)]
