"""Chooses how the stage is drawn: on the graphics chip if it can, otherwise the simple presence."""

from __future__ import annotations

import logging
import os

from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QSurfaceFormat
from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtWidgets import QWidget

from ..characters import DEFAULT_CHARACTER
from .presence import Presence
from .stage import STAGE_SHADER, Stage

log = logging.getLogger(__name__)

_GL_TRIANGLES = 4
_GL_RENDERER = 0x1F01
_SOFTWARE_DRIVERS = ("gdi generic", "llvmpipe", "swiftshader", "basic render")

VERTEX_SHADER = """#version 330 core
const vec2 corners[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
void main() { gl_Position = vec4(corners[gl_VertexID], 0.0, 1.0); }
"""

def _format() -> QSurfaceFormat:
    wanted = QSurfaceFormat()
    wanted.setRenderableType(QSurfaceFormat.RenderableType.OpenGL)
    wanted.setVersion(3, 3)
    wanted.setProfile(QSurfaceFormat.OpenGLContextProfile.CoreProfile)
    return wanted


def _build_program(parent) -> QOpenGLShaderProgram | None:
    program = QOpenGLShaderProgram(parent)
    built = (
        program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, VERTEX_SHADER)
        and program.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, STAGE_SHADER)
        and program.link()
    )
    if not built:
        log.warning("Graphics program did not build: %s", program.log())
        return None
    return program


def graphics_chip_ready() -> bool:
    """Tries everything on a hidden surface, so a failure here costs nothing."""
    if os.environ.get("ALPHA_SOFTWARE_DRAWING"):
        return False
    if QGuiApplication.platformName() == "offscreen":
        return False
    context = QOpenGLContext()
    context.setFormat(_format())
    if not context.create():
        return False
    surface = QOffscreenSurface()
    surface.setFormat(context.format())
    surface.create()
    try:
        if not surface.isValid() or not context.makeCurrent(surface):
            return False
        version = context.format().version()
        driver = (context.functions().glGetString(_GL_RENDERER) or "").lower()
        log.info("Graphics: OpenGL %s on %s", version, driver)
        if version < (3, 3) or any(name in driver for name in _SOFTWARE_DRIVERS):
            return False
        return _build_program(None) is not None
    finally:
        context.doneCurrent()


def make_presence(colours: dict[str, str] | None = None, character: str = DEFAULT_CHARACTER, parent: QWidget | None = None):
    if graphics_chip_ready():
        return Stage(colours, character, _format(), _build_program, parent)
    log.info("Drawing the presence on the processor.")
    return Presence(colours, parent)
