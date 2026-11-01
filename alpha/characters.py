"""The AI characters. APLHA is built in. Any others are the owner's own, in a private local file."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

LONGEST_IMAGE_SIDE = 1536
EXTRA_FILE = "extra_characters.json"


@dataclass(frozen=True)
class Character:
    id: str
    name: str
    title: str
    persona: str
    gemini_voice: str
    windows_voice: str
    scene: dict | None = None


CHARACTERS = {
    "aplha": Character(
        "aplha", "APLHA", "Your AI Core",
        "You present yourself as APLHA itself, an intelligence made of light: calm, warm, wise and attentive.",
        "Fenrir", "David",
        {"voice": (0.5, 0.225), "heart": (0.5, 0.7), "neck": 0.46},
    ),
}
DEFAULT_CHARACTER = "aplha"


def load_extras(path: Path) -> dict[str, Character]:
    """A missing or broken file simply means APLHA is the only character."""
    try:
        return {item["id"]: Character(**item) for item in json.loads(path.read_text(encoding="utf-8"))}
    except FileNotFoundError:
        return {}
    except (ValueError, TypeError, KeyError):
        log.warning("The extra characters could not be read.")
        return {}


if not os.environ.get("ALPHA_UNDER_TEST"):
    CHARACTERS.update(load_extras(paths.assets_dir() / "characters" / EXTRA_FILE))


def image_file(character_id: str) -> Path:
    folder = paths.data_dir() / "characters"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{character_id}.png"


def marks_file(character_id: str) -> Path:
    return image_file(character_id).with_suffix(".json")


def default_image(character_id: str) -> Path:
    return paths.assets_dir() / "characters" / f"{character_id}.png"


def has_own_picture(character_id: str) -> bool:
    return image_file(character_id).exists()


def picture_file(character_id: str) -> Path:
    """The owner's own picture if they set one, otherwise the built-in character."""
    return image_file(character_id) if has_own_picture(character_id) else default_image(character_id)


def _set_aside(path: Path) -> Path:
    return path.with_name(f"{path.stem}.removed{path.suffix}")


def remove_image(character_id: str) -> bool:
    """Returns to the built-in character. The owner's picture is kept aside, so it can come back."""
    image = image_file(character_id)
    if not image.exists():
        return False
    for path in (image, marks_file(character_id)):
        if path.exists():
            path.replace(_set_aside(path))
    return True


def can_restore_image(character_id: str) -> bool:
    return _set_aside(image_file(character_id)).exists()


def restore_image(character_id: str) -> bool:
    if not can_restore_image(character_id):
        return False
    for path in (image_file(character_id), marks_file(character_id)):
        if _set_aside(path).exists():
            _set_aside(path).replace(path)
    return True


def load_marks(character_id: str) -> dict | None:
    """Eye and mouth positions from 0 to 1 on the image, if they have been set."""
    own = marks_file(character_id)
    source = own if own.exists() or has_own_picture(character_id) else default_image(character_id).with_suffix(".json")
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def set_marks(character_id: str, left_eye, right_eye, mouth) -> None:
    gap = right_eye[0] - left_eye[0]
    marks = {
        "left_eye": list(left_eye), "right_eye": list(right_eye), "mouth": list(mouth),
        "mouth_width": gap * 0.55, "eye_width": gap * 0.36,
    }
    marks_file(character_id).write_text(json.dumps(marks, indent=2), encoding="utf-8")


def add_image(character_id: str, source: Path) -> None:
    """Keeps a resized copy, so the original can move or be deleted."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage

    picture = QImage(str(source))
    if picture.isNull():
        raise ValueError("That file could not be read as a picture.")
    if max(picture.width(), picture.height()) > LONGEST_IMAGE_SIDE:
        picture = picture.scaled(
            LONGEST_IMAGE_SIDE, LONGEST_IMAGE_SIDE, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    if not picture.save(str(image_file(character_id)), "PNG"):
        raise OSError("The picture could not be saved.")
