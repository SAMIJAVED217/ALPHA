"""
[ALPHA] ASTRA-style HUD — the centrepiece in the "astra" style.

Follows the ASTRA / APEX-UI interface (github.com/valoranttgm123-svg/ASTRA-AI-Agent,
built on github.com/RubenM1990/APEX-UI, both MIT): a gold ring orb with a cyan
particle core, a constellation of nodes wired to it, circuit traces running in
from the edges, and an equaliser + state label along the bottom. This is an
independent QPainter re-drawing of that design, not their code.

What it shows is real:
  * the nodes are ALPHA's own tool groups, and a node lights up — with pulses
    running out to it from the core — when ALPHA actually calls that tool
  * the equaliser and the particle core follow the live audio level
  * the label is ALPHA's state: STANDBY / LISTENING / PROCESSING / SPEAKING
"""
from __future__ import annotations

import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QLinearGradient,
                         QPainter, QPainterPath, QPen, QRadialGradient)

GOLD = QColor(255, 176, 58)
GOLD_HI = QColor(255, 226, 150)
CYAN = QColor(70, 222, 255)
TEXT = QColor(232, 238, 242)
DIM = QColor(120, 150, 165)
BG0 = QColor(14, 52, 78)      # centre of the backdrop
BG1 = QColor(4, 14, 26)       # edges

# label, kind ("info" cyan / "act" gold / "dim"), tool names that light it
NODES = (
    ("Web Search", "info", ("web_search",)),
    ("Browser", "info", ("browser_control",)),
    ("Weather", "info", ("weather_report",)),
    ("Memory", "info", ("save_memory", "recall_memory")),
    ("Files", "info", ("file_controller", "file_processor")),
    ("Vision", "info", ("screen_process", "close_camera")),
    ("Code", "act", ("code_helper", "dev_agent")),
    ("Media", "act", ("youtube_video", "video_player")),
    ("Plugins", "dim", ()),
    ("Reminders", "act", ("reminder",)),
    ("Messages", "act", ("send_message", "gmail_assistant")),
    ("System", "act", ("computer_settings", "computer_control", "system_status",
                       "manage_monitor", "undo")),
    ("Apps", "act", ("open_app", "desktop_control", "game_updater")),
)
_TOOL_TO_NODE = {t: i for i, (_, _, tools) in enumerate(NODES) for t in tools}
_PLUGIN_NODE = next(i for i, n in enumerate(NODES) if n[0] == "Plugins")
ACTIVE_FOR = 2.6      # seconds a node stays lit after its tool was called


def _a(col: QColor, a: float) -> QColor:
    c = QColor(col)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def node_for_tool(name: str) -> int:
    return _TOOL_TO_NODE.get(str(name or ""), _PLUGIN_NODE)


class AstraHud:
    REBUILD_EVERY = 0.35

    def __init__(self, seed: int = 7):
        self._seed = seed
        self._img: QImage | None = None
        self._key = None
        self._built_at = 0.0
        self._geo = None
        self._phase = 0.0
        self._eq = [0.0] * 44
        self._core_rng = random.Random(seed + 1)
        self._core_pts = [(self._core_rng.uniform(0, 2 * math.pi),
                           self._core_rng.uniform(-1, 1),
                           self._core_rng.uniform(0.15, 1.0) ** 0.6,
                           self._core_rng.uniform(0.4, 1.4))
                          for _ in range(260)]

    # ── geometry ──────────────────────────────────────────────────────────────
    def _layout(self, W: int, H: int):
        by = H - max(46.0, H * 0.075)             # status bar centre
        top, bottom = 22.0, by - 44.0             # band the constellation lives in
        cx, cy = W / 2.0, (top + bottom) / 2.0
        R = min(W * 0.20, (bottom - top) * 0.235)  # gold ring radius
        ry = min((bottom - top) / 2.0 - 12.0, R * 2.05)
        rx = max(R * 1.35, min(W / 2.0 - 96.0, R * 2.6))   # room for labels
        rng = random.Random(self._seed)
        nodes = []
        n = len(NODES)
        for i in range(n):
            ang = -math.pi / 2 + 2 * math.pi * (i + 0.5) / n
            j = 1.0 + rng.uniform(-0.09, 0.04)
            x = cx + math.cos(ang) * rx * j
            y = cy + math.sin(ang) * ry * j
            nodes.append((x, y, ang))
        return cx, cy, R, nodes, by

    def _build(self, W: int, H: int) -> QImage:
        rng = random.Random(self._seed + 3)
        cx, cy, R, nodes, by = self._layout(W, H)
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # backdrop: lit navy, brightest behind the orb
        g = QRadialGradient(QPointF(cx, cy * 0.85), max(W, H) * 0.75)
        g.setColorAt(0.0, BG0)
        g.setColorAt(0.55, QColor(8, 30, 48))
        g.setColorAt(1.0, BG1)
        p.fillRect(0, 0, W, H, QBrush(g))
        # soft diagonal "plasma" bands
        for k in range(5):
            band = QLinearGradient(0, 0, W, H)
            off = (k * 0.19 + 0.05) % 1.0
            band.setColorAt(max(0.0, off - 0.06), _a(CYAN, 0.0))
            band.setColorAt(off, _a(CYAN, 0.035))
            band.setColorAt(min(1.0, off + 0.06), _a(CYAN, 0.0))
            p.fillRect(0, 0, W, H, QBrush(band))
        # faint grid
        p.setPen(QPen(_a(CYAN, 0.035), 1))
        step = max(28, int(min(W, H) / 18))
        for x in range(0, W, step):
            p.drawLine(x, 0, x, H)
        for y in range(0, H, step):
            p.drawLine(0, y, W, y)

        # circuit traces running in from both edges, ending in dots
        for side in (-1, 1):
            for k in range(7):
                y0 = H * (0.18 + k * 0.105) + rng.uniform(-8, 8)
                x_edge = 6 if side < 0 else W - 6
                x1 = x_edge - side * rng.uniform(0.06, 0.16) * W
                y1 = y0 + rng.choice((-1, 1)) * rng.uniform(8, 24)
                x2 = x1 - side * rng.uniform(0.04, 0.12) * W
                path = QPainterPath(QPointF(x_edge, y0))
                path.lineTo(x1, y0)
                path.lineTo(x1 + side * -6, y1)
                path.lineTo(x2, y1)
                p.setPen(QPen(_a(CYAN, 0.10), 4))
                p.drawPath(path)
                p.setPen(QPen(_a(CYAN, 0.42), 1.2))
                p.drawPath(path)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_a(CYAN, 0.9))
                p.drawEllipse(QPointF(x_edge, y0), 2.6, 2.6)
                p.setBrush(_a(CYAN, 0.35))
                p.drawEllipse(QPointF(x2, y1), 1.8, 1.8)
                p.setBrush(Qt.BrushStyle.NoBrush)
        # a couple of loose dashes in the corners, like the reference
        p.setPen(QPen(_a(CYAN, 0.25), 1.4))
        for _ in range(4):
            x, y = rng.uniform(0.55, 0.95) * W, rng.uniform(0.05, 0.25) * H
            p.drawLine(QPointF(x, y), QPointF(x + 40, y + 22))

        # orbit rings around the orb
        for rr, a in ((R * 1.18, 0.10), (R * 1.32, 0.07), (R * 1.75, 0.05)):
            p.setPen(QPen(_a(CYAN, a), 1))
            p.drawEllipse(QPointF(cx, cy), rr, rr)

        # spokes: soft curves from the core out to every node
        for i, (x, y, ang) in enumerate(nodes):
            kind = NODES[i][1]
            col = DIM if kind == "dim" else CYAN
            mid = QPointF((cx + x) / 2 + math.cos(ang + 1.2) * R * 0.25,
                          (cy + y) / 2 + math.sin(ang + 1.2) * R * 0.25)
            path = QPainterPath(QPointF(cx, cy))
            path.quadTo(mid, QPointF(x, y))
            p.setPen(QPen(_a(col, 0.26 if kind != "dim" else 0.10), 1.1))
            p.drawPath(path)

        # node labels
        f = QFont("Segoe UI", max(8, int(min(W, H) * 0.019)))
        p.setFont(f)
        for i, (x, y, ang) in enumerate(nodes):
            label, kind, _ = NODES[i]
            right = math.cos(ang) >= 0
            p.setPen(QPen(DIM if kind == "dim" else TEXT, 1))
            box = QRectF(x + 14, y - 10, 160, 20) if right else QRectF(x - 174, y - 10, 160, 20)
            p.drawText(box, (Qt.AlignmentFlag.AlignLeft if right else Qt.AlignmentFlag.AlignRight)
                       | Qt.AlignmentFlag.AlignVCenter, label)
        p.end()

        self._geo = dict(cx=cx, cy=cy, R=R, nodes=nodes, by=by)
        return img

    # ── per frame ─────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, W: int, H: int, *, dt: float, amp: float,
              state: str, speaking: bool, muted: bool, tool_hits: dict):
        W, H = int(W), int(H)
        if W < 60 or H < 60:
            return
        key = (W, H)
        now = time.time()
        if self._img is None or (key != self._key and now - self._built_at > self.REBUILD_EVERY):
            self._img = self._build(W, H)
            self._key, self._built_at = key, now
        if key != self._key:
            p.drawImage(QRectF(0, 0, W, H), self._img)
            return
        p.drawImage(0, 0, self._img)

        g = self._geo
        cx, cy, R = g["cx"], g["cy"], g["R"]
        thinking = state in ("THINKING", "PROCESSING")
        listening = state == "LISTENING"
        live = (speaking or amp > 0.03) and not muted
        rate = 0.3 + (1.6 if thinking else 0.0) + (0.9 if speaking else 0.0)
        self._phase += max(0.0, min(0.1, dt)) * rate
        ph = self._phase
        breath = 0.5 + 0.5 * math.sin(now * 1.3)
        ring_col = QColor(150, 150, 150) if muted else GOLD

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # ── node activity: lit nodes and pulses running out from the core ──
        lit = {}
        for name, t0 in list(tool_hits.items()):
            age = now - t0
            if 0 <= age < ACTIVE_FOR:
                i = node_for_tool(name)
                lit[i] = max(lit.get(i, 0.0), 1.0 - age / ACTIVE_FOR)
        for i, (x, y, ang) in enumerate(g["nodes"]):
            kind = NODES[i][1]
            base = DIM if kind == "dim" else (CYAN if kind == "info" else GOLD)
            k = lit.get(i, 0.0)
            if k > 0:
                # the spoke lights up and carries pulses outward
                mid = QPointF((cx + x) / 2 + math.cos(ang + 1.2) * R * 0.25,
                              (cy + y) / 2 + math.sin(ang + 1.2) * R * 0.25)
                path = QPainterPath(QPointF(cx, cy))
                path.quadTo(mid, QPointF(x, y))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(_a(base, 0.55 * k), 1.6))
                p.drawPath(path)
                p.setPen(Qt.PenStyle.NoPen)
                for j in range(3):
                    s = ((now * 0.9 + j / 3.0) % 1.0)
                    pt = path.pointAtPercent(s)
                    p.setBrush(_a(GOLD_HI, k * (1 - s * 0.5)))
                    p.drawEllipse(pt, 2.6, 2.6)
            r = (9.0 if kind == "info" else 7.0) * (1.0 + 0.35 * k)
            glow = QRadialGradient(QPointF(x, y), r * 2.6)
            glow.setColorAt(0.0, _a(base, 0.30 + 0.5 * k))
            glow.setColorAt(1.0, _a(base, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(glow))
            p.drawEllipse(QPointF(x, y), r * 2.6, r * 2.6)
            p.setBrush(QColor(6, 22, 34))
            pen = QPen(_a(base, 0.95 if kind != "dim" else 0.5), 2.2 if kind == "info" else 1.8)
            if kind == "dim":
                pen.setStyle(Qt.PenStyle.DotLine)
            p.setPen(pen)
            p.drawEllipse(QPointF(x, y), r, r)
            if k > 0:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(_a(GOLD_HI if kind == "act" else QColor(220, 250, 255), k))
                p.drawEllipse(QPointF(x, y), r * 0.45, r * 0.45)

        # ── the orb ───────────────────────────────────────────────────────────
        # inner disc
        disc = QRadialGradient(QPointF(cx, cy), R)
        disc.setColorAt(0.0, QColor(20, 70, 96, 200))
        disc.setColorAt(0.85, QColor(10, 40, 60, 140))
        disc.setColorAt(1.0, QColor(10, 40, 60, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(disc))
        p.drawEllipse(QPointF(cx, cy), R, R)

        # fine inner geometry: spokes, crosshair, turning dashed arcs
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(_a(CYAN, 0.16), 1))
        for k in range(12):
            a = k * math.pi / 6 + 0.13
            p.drawLine(QPointF(cx, cy), QPointF(cx + math.cos(a) * R * 0.98, cy + math.sin(a) * R * 0.98))
        for rr, a, span, spd in ((0.30, 0.45, 70, 1.0), (0.48, 0.32, 110, -0.6), (0.66, 0.22, 50, 0.4)):
            rad = R * rr
            pen = QPen(_a(GOLD, a), 1.2)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (ph * 40 * spd) % 360
            for s in range(3):
                p.drawArc(box, int((base + s * 120) * 16), int(span * 16))

        # cyan particle core: a slowly turning cloud, brighter with the voice
        spin = ph * 0.9
        energy = 0.35 + 0.15 * breath + (0.6 * amp if live else 0.0) + (0.2 if thinking else 0.0)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        levels = ([], [], [])
        core_r = R * (0.42 + 0.06 * amp)
        for th, z, rad, spd in self._core_pts:
            a = th + spin * spd
            x3, z3 = math.cos(a) * rad, math.sin(a) * rad
            y3 = z * rad * 0.9
            depth = (z3 + 1) / 2
            levels[min(2, int(depth * 3))].append(QPointF(cx + x3 * core_r, cy + y3 * core_r))
        for lv, pts in enumerate(levels):
            pen = QPen(_a(CYAN, min(1.0, energy * (0.35 + lv * 0.3))), 1.6 + lv * 0.7)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPoints(pts)
        cg = QRadialGradient(QPointF(cx, cy), core_r * 0.9)
        cg.setColorAt(0.0, _a(CYAN, 0.35 * energy))
        cg.setColorAt(1.0, _a(CYAN, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(cg))
        p.drawEllipse(QPointF(cx, cy), core_r * 0.9, core_r * 0.9)

        # the gold ring: wide soft glow, body, bright inner edge
        glow_k = 0.55 + 0.25 * breath + (0.5 * amp if live else 0.0) + (0.25 if thinking else 0.0)
        for w, a in ((R * 0.20, 0.10), (R * 0.11, 0.18), (R * 0.05, 0.35)):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_a(ring_col, min(1.0, a * glow_k)), w))
            p.drawEllipse(QPointF(cx, cy), R, R)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(QPen(_a(ring_col, 0.95), max(2.0, R * 0.028)))
        p.drawEllipse(QPointF(cx, cy), R, R)
        p.setPen(QPen(_a(GOLD_HI if not muted else QColor(210, 210, 210), 0.85), 1.2))
        p.drawEllipse(QPointF(cx, cy), R - R * 0.02, R - R * 0.02)

        # ── bottom status: equaliser, core button, label ─────────────────────
        by = g["by"]
        n = len(self._eq)
        half = n // 2
        bw = max(2.0, min(4.0, W * 0.0045))
        gap = bw * 1.25
        btn_r = max(11.0, R * 0.12)
        eq_col = DIM if muted else (CYAN if listening else GOLD)
        for i in range(n):
            centre_dist = abs(i - (n - 1) / 2) / ((n - 1) / 2)
            env = (1 - centre_dist) ** 0.8
            idle = 0.08 + 0.05 * math.sin(now * 2.0 + i * 0.7)
            tgt = idle + (amp * 1.1 * env * (0.6 + 0.4 * math.sin(now * 13 + i * 1.7)) if live else 0.0)
            if thinking and not live:
                tgt += 0.25 * env * (0.5 + 0.5 * math.sin(now * 6 + i))
            self._eq[i] += (max(0.05, min(1.0, tgt)) - self._eq[i]) * 0.35
            h = 4 + self._eq[i] * 30
            if i < half:
                x = cx - btn_r - 12 - (half - i) * gap
            else:
                x = cx + btn_r + 12 + (i - half) * gap
            p.fillRect(QRectF(x, by - h / 2, bw, h), _a(eq_col, 0.85))
        line_w = half * gap + btn_r + 60
        pen = QPen(_a(eq_col, 0.35), 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.drawLine(QPointF(cx - line_w, by), QPointF(cx - half * gap - btn_r - 16, by))
        p.drawLine(QPointF(cx + half * gap + btn_r + 16, by), QPointF(cx + line_w, by))
        # core button
        p.setPen(QPen(_a(ring_col, 0.9), 2))
        p.setBrush(QColor(8, 10, 14))
        p.drawEllipse(QPointF(cx, by), btn_r, btn_r)
        dot = QRadialGradient(QPointF(cx, by), btn_r * 0.55)
        dot.setColorAt(0.0, _a(GOLD_HI, 1.0))
        dot.setColorAt(1.0, _a(ring_col, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(dot))
        dr = btn_r * (0.40 + 0.15 * breath + 0.2 * amp)
        p.drawEllipse(QPointF(cx, by), dr, dr)

        if muted:
            label = "MUTED"
        elif speaking:
            label = "SPEAKING"
        elif thinking:
            label = "PROCESSING"
        elif listening:
            label = "LISTENING"
        elif state in ("", "IDLE", "ONLINE", "READY", "STANDBY"):
            label = "STANDBY"
        else:
            label = str(state).upper()
        f = QFont("Courier New", max(8, int(min(W, H) * 0.017)), QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 4.0)
        p.setFont(f)
        p.setPen(QPen(_a(eq_col if not listening else CYAN, 0.95), 1))
        p.drawText(QRectF(0, by + btn_r + 8, W, 20), Qt.AlignmentFlag.AlignHCenter, label)
        # three state dots
        for k in range(3):
            on = (k == 0 and not (speaking or thinking)) or (k == 1 and thinking) or (k == 2 and speaking)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_a(GOLD, 0.9 if on else 0.3))
            p.drawEllipse(QPointF(cx - 8 + k * 8, by + btn_r + 34), 2, 2)
        p.restore()
