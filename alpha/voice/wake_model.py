"""The offline speech model for the wake words. Run with: python -m alpha.voice.wake_model"""

from __future__ import annotations

import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from .. import paths

MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"
_SMALLEST_REASONABLE_ZIP = 30_000_000


def model_dir() -> Path:
    return paths.data_dir() / "models" / MODEL_NAME


def is_ready() -> bool:
    """Looks at the files only, so it is safe and instant from any thread."""
    return (model_dir() / "am" / "final.mdl").exists()


def _unpack(archive: Path, target: Path) -> None:
    root = target.resolve()
    with zipfile.ZipFile(archive) as bundle:
        for entry in bundle.infolist():
            destination = (target / entry.filename).resolve()
            if not destination.is_relative_to(root):
                raise ValueError(f"Unsafe path in the model archive: {entry.filename}")
        bundle.extractall(target)


def fetch() -> Path:
    if is_ready():
        return model_dir()
    models = model_dir().parent
    models.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=models) as working:
        folder = Path(working)
        archive = folder / "model.zip"
        urllib.request.urlretrieve(MODEL_URL, archive)
        if archive.stat().st_size < _SMALLEST_REASONABLE_ZIP:
            raise ValueError("The downloaded model is too small to be complete.")
        unpacked = folder / "unpacked"
        _unpack(archive, unpacked)
        shutil.move(str(unpacked / MODEL_NAME), str(model_dir()))
    return model_dir()


if __name__ == "__main__":
    print(f"Model ready at {fetch()}")
