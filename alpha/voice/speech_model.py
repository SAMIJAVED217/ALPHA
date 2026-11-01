"""The offline speech recognition model. Run with: python -m alpha.voice.speech_model"""

from __future__ import annotations

from pathlib import Path

from .. import paths

MODEL_NAME = "faster-whisper-base"
MODEL_REPOSITORY = "Systran/faster-whisper-base"


def model_dir() -> Path:
    return paths.data_dir() / "models" / MODEL_NAME


def is_ready() -> bool:
    """Looks at the files only, so it is safe and instant from any thread."""
    return (model_dir() / "model.bin").exists()


def fetch() -> Path:
    if not is_ready():
        from huggingface_hub import snapshot_download

        snapshot_download(MODEL_REPOSITORY, local_dir=str(model_dir()))
    return model_dir()


if __name__ == "__main__":
    print(f"Model ready at {fetch()}")
