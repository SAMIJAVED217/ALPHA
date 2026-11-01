"""The holographic stage: a projected character on a platform, drawn on the graphics chip.

A character's picture goes on a dark background, so black simply becomes empty space.
A plain silhouette stands in only if a character's built-in picture is ever missing.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QCursor, QImage, QLinearGradient, QPainter, QPainterPath, QRadialGradient
from PySide6.QtOpenGL import QOpenGLTexture, QOpenGLVertexArrayObject
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

from .. import characters
from .presence import ACTIVE_INTERVAL_MS, PresenceMotion
from .style import BACKGROUND

_GL_TRIANGLES = 4
SPEAKING_LEVEL = 0.01


@dataclass(frozen=True)
class Scene:
    """Where a figure's lights sit on its picture, and the colours of its energy waves."""

    voice: tuple = (0.5, 0.085)
    heart: tuple = (0.5, 0.2)
    neck: float = 0.21
    cool: tuple = (0.12, 0.55, 1.0)
    warm: tuple = (1.0, 0.68, 0.22)


_FULL_BODY = Scene()


def _core_scene(character_id: str) -> Scene | None:
    """Characters with a scene stand in the machine hall, with waves in their own colours."""
    character = characters.CHARACTERS.get(character_id)
    return Scene(**character.scene) if character and character.scene else None

STAGE_SHADER = """#version 330 core
out vec4 outColour;
uniform sampler2D portrait;
uniform float portraitAspect;
uniform vec2 focus;
uniform float scale;
uniform vec3 tint;
uniform vec3 backdrop;
uniform float level;
uniform float orbit;
uniform float time;
uniform float blink;
uniform float mouthOpen;
uniform float speaking;
uniform float hasMarks;
uniform vec2 parallax;
uniform vec2 leftEye;
uniform vec2 rightEye;
uniform float eyeWidth;
uniform float scene;
uniform vec2 voicePoint;
uniform vec2 heartPoint;
uniform vec3 waveCool;
uniform vec3 waveWarm;
uniform float neckLine;

const float TAU = 6.2831853;
const float FIGURE_HEIGHT = 1.5;
const float FLOOR = -0.62;

float hash(vec2 v) {
    return fract(sin(dot(v, vec2(12.9898, 78.233))) * 43758.5453);
}

// Distance to a flat ring seen at an angle, centred on (0, y).
float tiltedRing(vec2 p, float y, float size, float flatness, float width) {
    vec2 q = vec2(p.x, (p.y - y) / flatness);
    return 1.0 - smoothstep(width * 0.5, width, abs(length(q) - size));
}

float arcsOf(vec2 p, float y, float flatness, float pieces, float solid, float turn) {
    float angle = atan((p.y - y) / flatness, p.x) + turn;
    return step(fract(angle / TAU * pieces), solid);
}

vec2 closeEye(vec2 uv, vec2 eye) {
    vec2 d = (uv - eye) / vec2(eyeWidth, eyeWidth * 0.5);
    uv.y -= blink * eyeWidth * 0.34 * max(0.0, 1.0 - dot(d, d));
    return uv;
}

// While talking the head nods, the body sways, and the arms lift on the stressed words.
// The head moves as one solid piece, because bending a picture of a face looks terrible.
vec2 speakingPose(vec2 uv) {
    uv.x += sin(time * 1.7) * 0.01 * speaking * (1.0 - uv.y);
    float head = smoothstep(neckLine + 0.03, neckLine - 0.03, uv.y);
    uv.y += sin(time * 3.7) * 0.004 * speaking * head;
    uv.x += sin(time * 2.3) * 0.004 * speaking * head;
    float side = uv.x < 0.5 ? -1.0 : 1.0;
    float arm = (1.0 - scene) * smoothstep(0.13, 0.24, abs(uv.x - 0.5)) * smoothstep(0.15, 0.3, uv.y) * smoothstep(0.62, 0.42, uv.y);
    float beat = 0.6 + 0.4 * sin(time * 3.1 + side * 1.2);
    uv.y += arm * speaking * beat * (0.012 + mouthOpen * 0.012);
    uv.x -= side * arm * speaking * beat * 0.008;
    return uv;
}

vec4 figure(vec2 p) {
    float width = FIGURE_HEIGHT * portraitAspect;
    vec2 place = vec2(sin(time * 0.31) * 0.006, FLOOR - 0.15 + sin(time * 0.9) * 0.004);
    // Breathing swells the chest a little more than the head.
    float breath = 1.0 + sin(time * 1.1) * 0.004;
    vec2 uv = vec2((p.x - place.x) / (width * breath) + 0.5, 1.0 - (p.y - place.y) / (FIGURE_HEIGHT * breath));
    if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) {
        return vec4(0.0);
    }
    uv.x += sin(time * 0.37) * 0.004 * (1.0 - uv.y);
    uv = speakingPose(uv);
    vec2 face = uv;
    if (hasMarks > 0.5) {
        uv = closeEye(uv, leftEye);
        uv = closeEye(uv, rightEye);
    }
    vec3 colour = texture(portrait, uv).rgb;
    if (hasMarks < 0.5) {
        // Robot faces light up with each syllable.
        vec2 visor = (face - voicePoint) / vec2(0.055, 0.04);
        colour *= 1.0 + 1.5 * mouthOpen * exp(-dot(visor, visor));
    }
    vec2 emblem = (face - heartPoint) / vec2(0.07, 0.06);
    float beat = pow(0.5 + 0.5 * sin(time * 2.2), 6.0);
    colour *= 1.0 + (0.8 * mouthOpen + 0.5 * scene * beat) * exp(-dot(emblem, emblem));
    float bright = max(colour.r, max(colour.g, colour.b));
    float solid = smoothstep(0.03, 0.16, bright);
    solid *= smoothstep(0.0, 0.04, uv.x) * smoothstep(1.0, 0.96, uv.x) * smoothstep(1.0, 0.97, uv.y);
    return vec4(colour, solid);
}

// A dark hall of machine racks running away on both sides.
vec3 machineHall(vec2 p) {
    float side = abs(p.x);
    float depth = 0.7 / (side + 0.08);
    float bays = abs(fract(depth * 2.4) - 0.5);
    float slats = step(0.45, abs(fract(p.y * depth * 2.2) - 0.5));
    float wall = smoothstep(0.35, 1.5, side);
    vec3 steel = vec3(0.02, 0.035, 0.06) * (0.5 + 0.5 * smoothstep(0.02, 0.12, bays));
    vec3 hall = vec3(0.006, 0.012, 0.024) + steel * wall * (0.7 + 0.5 * slats);
    return hall + vec3(0.02, 0.09, 0.2) * exp(-dot(p, p) * 0.9);
}

// Blue and gold threads of light leaving the figure on both sides. They rise with the voice.
vec3 energyWaves(vec2 p) {
    float side = abs(p.x);
    float swell = 0.05 + 0.2 * smoothstep(0.2, 1.7, side);
    swell *= 1.0 + 1.4 * level;
    // Only pixels the threads can reach do the work, which keeps idle use low.
    if (side < 0.2 || abs(p.y - 0.2) > swell * 1.5 + 0.08) {
        return vec3(0.0);
    }
    float mirrored = p.x > 0.0 ? 1.7 : 0.0;
    vec3 light = vec3(0.0);
    for (int i = 0; i < 9; i++) {
        float f = float(i);
        float wave = sin(side * 3.1 - time * (0.6 + f * 0.05) + f * 0.45 + mirrored);
        float ripple = sin(side * 7.3 + time * 0.45 + f * 0.6) * 0.32;
        float y = 0.2 + (f - 4.0) * 0.012 + swell * (wave + ripple) * (0.7 + f * 0.05);
        float d = abs(p.y - y);
        float glow = 0.0016 / (d + 0.0016);
        float gold = smoothstep(0.55, 0.85, 0.5 + 0.5 * sin(f * 2.4 + 1.0));
        light += mix(waveCool, waveWarm, gold) * glow;
    }
    return light * smoothstep(0.2, 0.55, side) * smoothstep(2.1, 1.0, side) * (0.5 + 0.5 * level + 0.15);
}

// One band of city at a given distance, with lit windows.
vec3 skyline(vec2 p, float reach, float tallest, float lit) {
    float x = p.x * reach;
    float block = floor(x);
    float height = 0.12 + hash(vec2(block, reach)) * tallest;
    if (p.y > FLOOR + height) {
        return vec3(0.0);
    }
    vec2 pane = vec2(fract(x) * 7.0, (p.y - FLOOR) * reach * 7.0);
    vec2 cell = floor(pane);
    float on = step(0.72, hash(cell + block * 13.0)) * step(0.25, fract(pane.x)) * step(0.3, fract(pane.y));
    float inside = smoothstep(0.0, 0.08, fract(x)) * smoothstep(1.0, 0.92, fract(x));
    vec3 light = mix(vec3(0.15, 0.75, 1.0), vec3(0.75, 0.35, 1.0), hash(vec2(block, 3.0)));
    if (hash(vec2(block, 9.0)) > 0.8) {
        light = vec3(1.0, 0.7, 0.3);
    }
    return vec3(0.02, 0.03, 0.08) * reach * 0.25 + light * on * inside * lit;
}

void main() {
    vec2 p = (gl_FragCoord.xy - focus) / scale;
    float top = clamp(p.y * 0.5 + 0.5, 0.0, 1.0);
    float feet = FLOOR - 0.15;

    // Everything is placed at its own depth, so nearer layers shift more as the viewer moves.
    vec2 far = p + parallax * 0.05;
    vec2 near = p - parallax * 0.035;
    vec2 back = p + parallax * 0.012;

    vec3 colour = backdrop + vec3(0.02, 0.05, 0.13) * (1.0 - top) + vec3(0.07, 0.02, 0.14) * top;
    colour += tint * 0.07 * exp(-dot(p, p) * 0.6);

    if (scene > 0.5) {
        colour = machineHall(far);
    } else if (far.y > FLOOR) {
        // Distant city, with haze between the layers.
        float haze = exp(-(far.y - FLOOR) * 1.6);
        vec3 distant = skyline(far + vec2(3.1, 0.0), 2.3, 0.75, 0.32);
        vec3 closer = skyline(far * 1.0 + vec2(7.7, 0.0), 4.2, 0.42, 0.16);
        vec3 city = distant + closer;
        colour = mix(colour, colour + city, smoothstep(0.0, 0.4, abs(p.x)) * 0.9);
        colour += mix(tint, vec3(0.5, 0.25, 1.0), 0.5) * haze * 0.09;
    }

    // Polished floor, with lines running away from the viewer.
    if (back.y < FLOOR) {
        float depth = 0.22 / (FLOOR + 0.03 - back.y);
        vec2 grid = abs(fract(vec2(back.x * depth * 2.2, depth * 3.0 + time * 0.03)) - 0.5);
        float line = 1.0 - smoothstep(0.0, 0.02 * depth, min(grid.x, grid.y));
        colour = mix(colour, vec3(0.01, 0.015, 0.04), 0.7);
        colour += tint * 0.14 * line * smoothstep(5.0, 0.5, depth);
        colour += tint * 0.16 * exp(-pow(back.x * 1.4, 2.0)) * smoothstep(-1.1, FLOOR, back.y);
    }

    // Light from the projector, brighter at its edges like a real cone in haze.
    float beamWidth = mix(0.56, 0.3, top);
    float edge = abs(back.x) / beamWidth;
    float beam = (exp(-pow(edge, 4.0)) * (0.55 + 0.45 * edge * edge)) * smoothstep(-0.82, -0.6, back.y) * smoothstep(1.02, 0.8, back.y);
    float streaks = 0.72 + 0.28 * sin(back.x * 70.0 + sin(back.y * 3.0 + time * 0.6) * 2.0);
    colour += tint * beam * streaks * (0.16 + 0.1 * level);

    // Projector overhead, a solid dark ring with lit edges.
    float housing = length(vec2(near.x, (near.y - 0.97) / 0.17));
    if (housing < 0.66 && scene < 0.5) {
        colour = mix(colour, vec3(0.02, 0.03, 0.06), 0.9);
    }
    for (int i = 0; i < 3 && scene < 0.5; i++) {
        float size = 0.64 - float(i) * 0.15;
        float piece = tiltedRing(near, 0.97, size, 0.17, 0.014);
        colour += tint * piece * (i == 0 ? 1.5 : 0.8 * arcsOf(near, 0.97, 0.17, 5.0, 0.7, orbit * (i == 1 ? 1.2 : -1.8)));
    }

    // Back halves of the orbiting rings, partly hidden behind the figure.
    for (int i = 0; i < 2 && scene < 0.5; i++) {
        float y = i == 0 ? -0.2 : 0.2;
        float size = i == 0 ? 0.74 : 0.6;
        colour += tint * 0.55 * tiltedRing(back, y, size, 0.2, 0.011) * step(y, back.y);
    }

    if (scene > 0.5) {
        colour += energyWaves(back);
    }

    // Platform: side wall first, then the top surface.
    float across = near.x / 0.82;
    float rim = sqrt(max(0.0, 1.0 - across * across)) * 0.82 * 0.2;
    float underTop = -0.82 - rim;
    if (abs(across) < 1.0 && near.y < underTop + 0.002 && near.y > underTop - mix(0.085, 0.2, scene)) {
        float metal = 0.07 + 0.55 * pow(1.0 - abs(across), 2.0) + 0.3 * pow(max(0.0, 1.0 - abs(across - 0.35) * 2.6), 6.0);
        float band = smoothstep(0.012, 0.0, abs(near.y - (underTop - 0.05)));
        colour = vec3(0.1, 0.13, 0.2) * metal + tint * band * 1.2;
    }
    float disc = length(vec2(near.x, (near.y + 0.82) / 0.2));
    if (disc < 0.82) {
        float shine = 0.05 + 0.1 * pow(max(0.0, 1.0 - length(near - vec2(0.25, -0.79)) * 1.7), 3.0);
        colour = vec3(0.03, 0.04, 0.08) + vec3(0.5, 0.6, 0.85) * shine + tint * 0.3 * smoothstep(0.66, 0.0, disc);
    }
    for (int i = 0; i < 4; i++) {
        float size = 0.8 - float(i) * 0.15;
        float piece = tiltedRing(near, -0.82, size, 0.2, i == 0 ? 0.022 : 0.012);
        float dashed = i == 0 ? 1.0 : arcsOf(near, -0.82, 0.2, 6.0 + float(i) * 3.0, 0.65, orbit * (i % 2 == 0 ? 1.0 : -1.4));
        colour += mix(tint, vec3(1.0), 0.2) * piece * dashed * (1.0 + 0.6 * level);
    }

    // The figure's reflection in the polished surfaces below its feet.
    if (p.y < feet) {
        vec2 mirrored = vec2(p.x + sin(p.y * 40.0 + time) * 0.002, feet * 2.0 - p.y);
        vec4 reflected = figure(mirrored);
        colour += reflected.rgb * reflected.a * 0.42 * exp(-(feet - p.y) * 7.0);
    }

    vec4 body = figure(p);
    if (body.a > 0.0) {
        float scan = 0.95 + 0.05 * sin(gl_FragCoord.y * 1.4 + time * 4.0);
        float sweep = exp(-pow(p.y - (fract(time * 0.12) * 2.6 - 1.3), 2.0) * 160.0);
        vec3 glass = mix(body.rgb, tint * dot(body.rgb, vec3(0.3, 0.6, 0.1)) * 1.7, 0.08);
        colour = mix(colour, glass * scan * 1.15 + tint * sweep * 0.35, body.a);
    }

    // Front halves of the orbiting rings pass in front of the figure.
    for (int i = 0; i < 2 && scene < 0.5; i++) {
        float y = i == 0 ? -0.2 : 0.2;
        float size = i == 0 ? 0.74 : 0.6;
        float ring = tiltedRing(near, y, size, 0.2, 0.012) * arcsOf(near, y, 0.2, 3.0, 0.8, orbit * (i == 0 ? 1.0 : -1.3));
        colour += mix(tint, vec3(1.0), 0.35) * 1.2 * ring * step(near.y, y) * (0.8 + 0.5 * level);
    }

    // Motes of light at two depths, the nearer ones larger and softer.
    for (int i = 0; i < 2; i++) {
        vec2 layer = i == 0 ? back : near;
        float density = i == 0 ? 16.0 : 8.0;
        vec2 cell = vec2(layer.x * density, layer.y * density * 0.56 - time * (i == 0 ? 0.2 : 0.34));
        vec2 slot = floor(cell);
        if (hash(slot + float(i)) > 0.955) {
            vec2 spot = fract(cell) - 0.5 + vec2(hash(slot + 3.0), hash(slot + 7.0)) * 0.5 - 0.25;
            colour += tint * (1.0 - smoothstep(0.0, i == 0 ? 0.09 : 0.14, length(spot))) * 0.7 * (0.2 + beam);
        }
    }

    // Filmic finish: highlights roll off softly, and the corners fall away.
    colour *= 1.0 - 0.32 * dot(p * 0.42, p * 0.42);
    colour = vec3(1.0) - exp(-colour * 1.45);
    outColour = vec4(colour + (hash(gl_FragCoord.xy) - 0.5) / 255.0, 1.0);
}
"""

BLINK_EVERY_SECONDS = 4.3
BLINK_SECONDS = 0.16


def blink_amount(seconds: float) -> float:
    moment = seconds % BLINK_EVERY_SECONDS
    return max(0.0, 1.0 - abs(moment - BLINK_SECONDS / 2) / (BLINK_SECONDS / 2))


def silhouette(_character_id: str = "") -> QImage:
    """A plain stand-in figure, used until a real picture is added."""
    width, height = 640, 960
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("black"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    body = QLinearGradient(0, 0, 0, height)
    body.setColorAt(0.0, QColor("#a9bbdc"))
    body.setColorAt(1.0, QColor("#2b3b5e"))
    painter.setBrush(body)
    painter.setPen(Qt.PenStyle.NoPen)
    middle = width / 2
    shoulders = 195
    torso = QPainterPath()
    torso.moveTo(middle - 46, 250)
    torso.cubicTo(middle - 60, 300, middle - shoulders, 300, middle - shoulders, 390)
    torso.lineTo(middle - shoulders + 40, 960)
    torso.lineTo(middle + shoulders - 40, 960)
    torso.lineTo(middle + shoulders, 390)
    torso.cubicTo(middle + shoulders, 300, middle + 60, 300, middle + 46, 250)
    torso.closeSubpath()
    painter.drawPath(torso)
    painter.drawEllipse(QPointF(middle, 170), 84, 108)
    glow = QRadialGradient(middle, 400, 34)
    glow.setColorAt(0.0, QColor("#ffffff"))
    glow.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(glow)
    painter.drawEllipse(QPointF(middle, 400), 34, 34)
    painter.end()
    return image


def portrait_for(character_id: str) -> QImage:
    picture = characters.picture_file(character_id)
    if picture.exists():
        loaded = QImage(str(picture))
        if not loaded.isNull():
            return loaded
    return silhouette(character_id)


def follow_voice(now: float, wanted: float, rising: float, falling: float) -> float:
    """Moves towards the sound, opening faster than it closes, the way a mouth does."""
    return now + (wanted - now) * (rising if wanted > now else falling)


class Stage(PresenceMotion, QOpenGLWidget):
    resting_interval_ms = ACTIVE_INTERVAL_MS

    def __init__(self, colours: dict[str, str] | None, character_id: str, surface_format, build_program,
                 parent: QWidget | None = None):
        QOpenGLWidget.__init__(self, parent)
        self._begin_motion(colours)
        self.setFormat(surface_format)
        self._build_program = build_program
        self._program = None
        self._vertices: QOpenGLVertexArrayObject | None = None
        self._texture: QOpenGLTexture | None = None
        self._aspect = 1.0
        self._character = character_id
        self._marks = None
        self._stale_texture = True
        self._started = time.monotonic()
        self._voice_source = lambda: 0.0
        self._mouth = 0.0
        self._speaking = 0.0
        self._view = [0.0, 0.0]
        self.focus_x: float | None = None
        self._scene: Scene | None = None
        backdrop = QColor(BACKGROUND)
        self._backdrop = (backdrop.redF(), backdrop.greenF(), backdrop.blueF())

    def set_character(self, character_id: str) -> None:
        self._character = character_id
        self._stale_texture = True
        self.update()

    def set_voice_source(self, source) -> None:
        self._voice_source = source

    def _request_frame(self) -> None:
        voice = self._voice_source()
        self._mouth = follow_voice(self._mouth, min(1.0, voice * 3.0), 0.6, 0.3)
        self._speaking = follow_voice(self._speaking, 1.0 if voice > SPEAKING_LEVEL else 0.0, 0.12, 0.04)
        self._follow_viewer()
        self.update()

    def _follow_viewer(self) -> None:
        """The pointer stands in for the viewer's head, which gives the layers real depth."""
        spot = self.mapFromGlobal(QCursor.pos())
        goal_x = max(-1.0, min(1.0, spot.x() / max(1, self.width()) * 2.0 - 1.0))
        goal_y = max(-1.0, min(1.0, 1.0 - spot.y() / max(1, self.height()) * 2.0))
        self._view[0] += (goal_x - self._view[0]) * 0.08
        self._view[1] += (goal_y - self._view[1]) * 0.08

    def initializeGL(self) -> None:
        self._program = self._build_program(self)
        self._vertices = QOpenGLVertexArrayObject(self)
        self._vertices.create()
        self._texture = None
        self._stale_texture = True

    def _load_portrait(self) -> None:
        if self._texture is not None:
            self._texture.destroy()
        picture = portrait_for(self._character)
        self._aspect = picture.width() / picture.height()
        self._marks = characters.load_marks(self._character)
        self._scene = None if characters.has_own_picture(self._character) else _core_scene(self._character)
        self._texture = QOpenGLTexture(picture)
        self._texture.setMinMagFilters(QOpenGLTexture.Filter.Linear, QOpenGLTexture.Filter.Linear)
        self._texture.setWrapMode(QOpenGLTexture.WrapMode.ClampToEdge)
        self._stale_texture = False

    def paintGL(self) -> None:
        program = self._program
        if program is None:
            return
        if self._stale_texture:
            self._load_portrait()
        model = self.model
        ratio = self.devicePixelRatioF()
        seconds = time.monotonic() - self._started
        centre = self.width() / 2 if self.focus_x is None else self.focus_x
        program.bind()
        self._vertices.bind()
        self._texture.bind(0)
        program.setUniformValue1i("portrait", 0)
        program.setUniformValue1f("portraitAspect", self._aspect)
        program.setUniformValue("focus", centre * ratio, self.height() * ratio * 0.55)
        program.setUniformValue1f("scale", self.height() * ratio * 0.5)
        program.setUniformValue("tint", *(channel / 255.0 for channel in model.colour))
        program.setUniformValue("backdrop", *self._backdrop)
        program.setUniformValue1f("level", model.level)
        program.setUniformValue1f("orbit", model.orbit_angle)
        program.setUniformValue("parallax", *self._view)
        scene = self._scene or _FULL_BODY
        program.setUniformValue1f("scene", 1.0 if self._scene else 0.0)
        program.setUniformValue("voicePoint", *scene.voice)
        program.setUniformValue("heartPoint", *scene.heart)
        program.setUniformValue1f("neckLine", scene.neck)
        program.setUniformValue("waveCool", *scene.cool)
        program.setUniformValue("waveWarm", *scene.warm)
        program.setUniformValue1f("time", seconds % 3600.0)
        program.setUniformValue1f("blink", blink_amount(seconds))
        program.setUniformValue1f("mouthOpen", self._mouth)
        program.setUniformValue1f("speaking", self._speaking)
        marks = self._marks
        program.setUniformValue1f("hasMarks", 1.0 if marks else 0.0)
        if marks:
            program.setUniformValue("leftEye", *marks["left_eye"])
            program.setUniformValue("rightEye", *marks["right_eye"])
            program.setUniformValue1f("eyeWidth", marks["eye_width"])
        self.context().functions().glDrawArrays(_GL_TRIANGLES, 0, 3)
        self._texture.release(0)
        self._vertices.release()
        program.release()
        self.frames_drawn += 1
