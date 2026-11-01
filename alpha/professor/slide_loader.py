"""Turns chosen PDFs and pictures into numbered slides, and slides into files for Gemini.

Gemini reads the pages itself, because plain text extraction breaks on Korean and on
slides that are pictures. Rendering to pictures is the fallback.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader, PdfWriter

PICTURE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
LARGEST_REQUEST_BYTES = 45_000_000


class SlideError(Exception):
    """The slides could not be used. The message is shown to the owner."""


@dataclass(frozen=True)
class Slide:
    path: Path
    page: int | None = None  # None for a picture, which is one slide.


def load(paths: list[str | Path]) -> list[Slide]:
    slides: list[Slide] = []
    for item in paths:
        path = Path(item)
        if path.suffix.lower() == ".pdf":
            try:
                slides += [Slide(path, page) for page in range(len(PdfReader(path).pages))]
            except Exception as error:
                raise SlideError(f"{path.name} could not be opened ({type(error).__name__}).") from error
        elif path.suffix.lower() in PICTURE_TYPES and path.is_file():
            slides.append(Slide(path))
        else:
            raise SlideError(f"{path.name} is not a PDF or picture that can be used.")
    return slides


def _pdf_of(path: Path, pages: list[int]) -> bytes:
    reader = PdfReader(path)
    writer = PdfWriter()
    for page in pages:
        writer.add_page(reader.pages[page])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def as_files(slides: list[Slide]) -> list[tuple[bytes, str]]:
    """Neighbouring pages of one PDF stay together as a small PDF, in slide order."""
    files: list[tuple[bytes, str]] = []
    run: list[Slide] = []

    def finish_run() -> None:
        if run:
            files.append((_pdf_of(run[0].path, [slide.page for slide in run]), "application/pdf"))
            run.clear()

    for slide in slides:
        if slide.page is None:
            finish_run()
            files.append((slide.path.read_bytes(), PICTURE_TYPES[slide.path.suffix.lower()]))
        else:
            if run and run[0].path != slide.path:
                finish_run()
            run.append(slide)
    finish_run()
    if sum(len(data) for data, _ in files) > LARGEST_REQUEST_BYTES:
        raise SlideError("These slides are too large to send at once. Lower chunk_pages in the config.")
    return files


def as_pictures(slides: list[Slide], dpi: int) -> list[tuple[bytes, str]]:
    import cv2
    import pypdfium2

    files: list[tuple[bytes, str]] = []
    for slide in slides:
        if slide.page is None:
            files.append((slide.path.read_bytes(), PICTURE_TYPES[slide.path.suffix.lower()]))
            continue
        document = pypdfium2.PdfDocument(slide.path)
        try:
            page = document[slide.page]
            picture = page.render(scale=dpi / 72).to_numpy()
            page.close()
            done, encoded = cv2.imencode(".png", picture)
            if not done:
                raise SlideError(f"Page {slide.page + 1} of {slide.path.name} could not be drawn.")
            files.append((encoded.tobytes(), "image/png"))
        finally:
            document.close()
    return files
