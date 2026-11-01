"""Draws LaTeX from the notes as small pictures, because the notes view cannot show it.

Equations come out as pictures rather than text. Anything matplotlib cannot draw, such
as matrices and aligned equations, becomes the closest plain text instead.
"""

from __future__ import annotations

import hashlib
import io
import logging
import re
import threading
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

COLOUR = "#eaf1ff"
INLINE_SIZE = 14
DISPLAY_SIZE = 18
SHARPNESS = 2
_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)
_DISPLAY = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]", re.DOTALL)
_INLINE = re.compile(r"(?<![\\$\w])\$(?=\S)([^$\n]+?)(?<=\S)\$(?![\d$])|\\\((.+?)\\\)")
_BARE_POWER = re.compile(r"(\\[ ,;:!]|^|\s)\^")
_SWAPS = (
    (r"\dfrac", r"\frac"), (r"\tfrac", r"\frac"), (r"\boldsymbol", r"\mathbf"), (r"\bm", r"\mathbf"),
    (r"\displaystyle", ""), (r"\textbf", r"\mathbf"), (r"\quad", r"\ \ "), (r"\qquad", r"\ \ \ "),
)
_PLAIN_SWAPS = (
    (r"^\circ", "°"), (r"\circ", "°"), (r"\times", "×"), (r"\cdot", "·"), (r"\approx", "≈"), (r"\leq", "≤"),
    (r"\le", "≤"), (r"\geq", "≥"), (r"\ge", "≥"), (r"\neq", "≠"), (r"\pm", "±"), (r"\rightarrow", "→"),
    (r"\to", "→"), (r"\Rightarrow", "⇒"), (r"\Delta", "Δ"), (r"\mu", "µ"), (r"\Omega", "Ω"), (r"\ ", " "),
    (r"\,", " "), (r"\\", "; "),
)
_SIMPLE_PIECES = re.compile(r"\\(text|mathrm)\{[^{}]*\}|\{,\}")
_lock = threading.Lock()


@dataclass(frozen=True)
class Prepared:
    markdown: str
    pictures: dict[str, bytes] = field(default_factory=dict)


def _tidied(latex: str) -> str:
    latex = latex.strip()
    for old, new in _SWAPS:
        latex = latex.replace(old, new)
    return _BARE_POWER.sub(lambda found: f"{found.group(1)}{{}}^", latex)


def _is_simple(latex: str) -> bool:
    """Things like 450 °C read better as normal text, in line with the sentence around them."""
    rest = _SIMPLE_PIECES.sub("", latex)
    for old, _new in _PLAIN_SWAPS:
        rest = rest.replace(old, "")
    return re.search(r"[\\_^{}]", rest) is None


def plain(latex: str) -> str:
    """Readable text for an equation that is better not drawn, or cannot be."""
    for old, new in _PLAIN_SWAPS:
        latex = latex.replace(old, new)
    latex = re.sub(r"\\(text|mathrm|mathbf|operatorname)\{([^{}]*)\}", r"\2", latex)
    latex = re.sub(r"\\(begin|end)\{[^{}]*\}", " ", latex)
    latex = re.sub(r"\\(left|right)", "", latex)
    latex = re.sub(r"\\([a-zA-Z]+)", r"\1", latex)
    latex = latex.replace("{", "").replace("}", "").replace("&", " ")
    return " ".join(latex.split())


def _draw(latex: str, size: int) -> bytes:
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.font_manager import FontProperties
    from matplotlib.mathtext import MathTextParser

    text = f"${_tidied(latex)}$"
    font = FontProperties(size=size)
    with _lock:
        shape = MathTextParser("path").parse(text, 72, prop=font)
        figure = Figure(figsize=(shape.width / 72, shape.height / 72))
        FigureCanvasAgg(figure)
        figure.text(0, shape.depth / shape.height, text, fontproperties=font, color=COLOUR)
        out = io.BytesIO()
        figure.savefig(out, dpi=72 * SHARPNESS, format="png", transparent=True)
    return out.getvalue()


def _convert(segment: str, pictures: dict[str, bytes]) -> str:
    def replace(latex: str, size: int, display: bool) -> str:
        if not display and _is_simple(latex):
            return plain(latex)
        name = hashlib.sha1(f"{size}{latex}".encode()).hexdigest() + ".png"
        try:
            if not latex.isascii() and re.search(r"[^\x00-\u024f\u0370-\u03ff\u2000-\u22ff]", latex):
                raise ValueError("has letters the equation font does not cover")
            pictures[name] = _draw(latex, size)
        except Exception as error:
            log.info("Showing an equation as text: %s", type(error).__name__)
            return plain(latex)
        return f"\n\n![equation]({name})\n\n" if display else f"![equation]({name})"

    segment = _DISPLAY.sub(lambda found: replace(found[1] or found[2], DISPLAY_SIZE, True), segment)
    return _INLINE.sub(lambda found: replace(found[1] or found[2], INLINE_SIZE, False), segment)


def prepare(markdown: str) -> Prepared:
    """Runs off the interface thread, since the first drawing loads matplotlib. Code is left alone."""
    pictures: dict[str, bytes] = {}
    pieces = _CODE.split(markdown)
    for index in range(0, len(pieces), 2):
        pieces[index] = _convert(pieces[index], pictures)
    return Prepared("".join(pieces), pictures)
