"""
[ALPHA] Particle figure — the HUD centrepiece in the "particle" style.

A head-and-shoulders bust drawn as glowing contour lines and particles, with a
warm core in the face and particle mountain ranges either side.

Same budget as the other centrepieces: software QPainter, no GPU, no new
packages. Everything static (mountains, body contours, particle halo, bloom) is
rendered once per size/colour into an image; each frame only draws the parts
that move: the face rings, the warm core, a few hundred twinkling particles,
the chest light and the status line.

What moves means something:
  * the face rings ripple and the core brightens with the audio level
    (your voice while listening, the assistant's while speaking)
  * the rings turn faster while thinking, and the neck veins pulse
  * muted dims the whole figure
"""
from __future__ import annotations

import colorsys
import math
import random
import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (QBrush, QColor, QFont, QImage, QPainter,
                         QPainterPath, QPen, QRadialGradient, QLinearGradient)

WARM = QColor(255, 150, 40)     # the face core
GOLD = QColor(255, 196, 80)     # veins in the mountains and neck


def _hsv(col: QColor):
    return colorsys.rgb_to_hsv(col.redF(), col.greenF(), col.blueF())


def _from_hsv(h, s, v, a=255) -> QColor:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return QColor(int(r * 255), int(g * 255), int(b * 255), int(a))


def _with_alpha(col: QColor, a: float) -> QColor:
    c = QColor(col)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def _mix(a: QColor, b: QColor, k: float) -> QColor:
    k = max(0.0, min(1.0, k))
    return QColor(int(a.red() + (b.red() - a.red()) * k),
                  int(a.green() + (b.green() - a.green()) * k),
                  int(a.blue() + (b.blue() - a.blue()) * k))


def _ridge(rng: random.Random, n: int, rough: float) -> list[float]:
    """1-D midpoint displacement: a natural mountain ridge, values ~0..1."""
    size = 1
    while size < n:
        size *= 2
    pts = [0.0] * (size + 1)
    pts[0], pts[size] = rng.uniform(0.2, 0.6), rng.uniform(0.2, 0.6)
    step, amp = size, 0.5
    while step > 1:
        half = step // 2
        for i in range(half, size, step):
            pts[i] = (pts[i - half] + pts[i + half]) / 2 + rng.uniform(-amp, amp)
        step, amp = half, amp * rough
    lo, hi = min(pts), max(pts)
    return [(p - lo) / (hi - lo or 1) for p in pts[: n]]


class ParticleFigure:
    REBUILD_EVERY = 0.35          # s — at most this often while resizing

    def __init__(self, seed: int = 56):
        self._seed = seed
        self._img: QImage | None = None
        self._key = None
        self._built_at = 0.0
        self._geo = None          # geometry for the animated parts
        self._phase = 0.0
        self._face_img: QImage | None = None   # face lines, redrawn only when needed
        self._face_t = 0.0
        self._face_live = False
        self._face_muted = False

    # ── geometry ──────────────────────────────────────────────────────────────
    @staticmethod
    def _frame(W: float, H: float):
        F = min(H * 0.98, W * 1.05)            # figure height
        return F, W / 2.0, H - F                # scale, centre x, top y

    @staticmethod
    def _head_pt(th: float, scale: float = 1.0):
        """Head outline in figure units. th=0 top, pi chin."""
        s, c = math.sin(th), math.cos(th)
        taper = 1.0 - 0.34 * ((1.0 - c) / 2.0) ** 2.2
        return 0.165 * s * taper * scale, 0.30 - 0.222 * c * scale + 0.02 * (1 - scale)

    @staticmethod
    def _shoulder(k: float):
        """Right shoulder line as points (u, v); k=0 outer edge, k>0 nested inward."""
        s = 1.0 - k
        cx, cy = 0.0, 1.10
        out = []
        for (p0, c1, c2, p1), n in ((((0.070, 0.575), (0.082, 0.665), (0.215, 0.672), (0.315, 0.725)), 22),
                                    (((0.315, 0.725), (0.378, 0.758), (0.392, 0.855), (0.398, 1.04)), 16)):
            for i in range(n):
                t = i / (n - 1)
                mt = 1 - t
                u = mt**3 * p0[0] + 3 * mt**2 * t * c1[0] + 3 * mt * t**2 * c2[0] + t**3 * p1[0]
                v = mt**3 * p0[1] + 3 * mt**2 * t * c1[1] + 3 * mt * t**2 * c2[1] + t**3 * p1[1]
                out.append((u, v))
        return [(cx + (u - cx) * s, cy + (v - cy) * s) for u, v in out]

    # ── static layer ──────────────────────────────────────────────────────────
    def _build(self, W: int, H: int, main: QColor) -> QImage:
        rng = random.Random(self._seed)
        F, cx, top = self._frame(W, H)

        def P(u, v):
            return QPointF(cx + u * F, top + v * F)

        h, s, v = _hsv(main)
        cool = _from_hsv(h, min(1.0, s * 0.9), 1.0)
        white = _from_hsv(h, 0.25, 1.0)

        # 1) background: a lit navy, hue taken from the theme and nudged to blue
        img = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        bg = QPainter(img)
        g = QLinearGradient(0, 0, 0, H)
        bh = h + 0.06
        g.setColorAt(0.0, _from_hsv(bh, 0.88, 0.30))
        g.setColorAt(0.55, _from_hsv(bh, 0.92, 0.20))
        g.setColorAt(1.0, _from_hsv(bh, 0.95, 0.10))
        bg.fillRect(0, 0, W, H, QBrush(g))
        bg.end()

        # 2) emissive layer on black: everything that glows, so it can bloom
        em = QImage(W, H, QImage.Format.Format_ARGB32_Premultiplied)
        em.fill(QColor(0, 0, 0))
        p = QPainter(em)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        buckets: dict = {}

        def dot(x, y, col: QColor, a: float, size: float = 1.4):
            key = (col.rgb(), round(min(1.0, a) * 8) / 8, size)
            buckets.setdefault(key, []).append(QPointF(x, y))

        def flush_dots():
            for (rgb, a, size), pts in buckets.items():
                if a <= 0:
                    continue
                c = QColor.fromRgb(rgb)
                c.setAlphaF(a)
                pen = QPen(c, size)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                p.setPen(pen)
                p.drawPoints(pts)
            buckets.clear()

        def line(points, col: QColor, a: float, w: float):
            if len(points) < 2:
                return
            path = QPainterPath(points[0])
            for q in points[1:]:
                path.lineTo(q)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_with_alpha(col, a), w, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)

        # stars
        for _ in range(int(W * H / 2600)):
            dot(rng.uniform(0, W), rng.uniform(0, H * 0.7), white, rng.uniform(0.08, 0.35), 1.0)
        flush_dots()

        # 3) mountains, left and right of the figure
        n = 257
        for side in (-1, 1):
            for layer, (base, height, bright) in enumerate(
                    ((0.60, 0.40, 1.0), (0.72, 0.26, 0.55), (0.82, 0.16, 0.35))):
                prof = _ridge(rng, n, 0.55)
                ridge = []
                for i in range(n):
                    fx = i / (n - 1)                       # 0 at figure, 1 at edge
                    x = cx + side * (0.15 * F + fx * (W / 2 + 10 - 0.15 * F))
                    rise = 0.45 + 0.55 * fx ** 0.9
                    y = H * base - H * height * (0.25 + 0.75 * prof[i]) * (0.35 + 0.65 * rise)
                    ridge.append(QPointF(x, y))
                # particles hanging below the ridge, densest at the crest
                for _ in range(int((W / 2) * (2.6 if layer == 0 else 1.5))):
                    i = rng.randrange(n)
                    rp = ridge[i]
                    d = rng.expovariate(1.0 / (H * 0.055))
                    if rp.y() + d > H:
                        continue
                    a = bright * (0.85 * math.exp(-d / (H * 0.09)) + 0.08)
                    col = white if rng.random() < 0.15 else cool
                    dot(rp.x() + rng.uniform(-2, 2), rp.y() + d, col, a,
                        2.0 if rng.random() < 0.08 else 1.3)
                    if rng.random() < 0.04:                # spray above the crest
                        dot(rp.x() + rng.uniform(-4, 4), rp.y() - rng.expovariate(1 / 6.0),
                            white, 0.6 * bright, 1.2)
                flush_dots()
                line(ridge, cool, 0.18 * bright, 5.0)
                line(ridge, white, 0.75 * bright, 1.2)
                # facet lines down the slopes from the higher points
                peaks = [i for i in range(4, n - 4, 6)
                         if ridge[i].y() <= min(r.y() for r in ridge[i - 4:i + 5]) + 0.5]
                for i in peaks:
                    pk = ridge[i]
                    for dirn in (-1, 1):
                        if rng.random() < 0.6:
                            end = QPointF(pk.x() + dirn * rng.uniform(8, 40),
                                          pk.y() + rng.uniform(20, 70))
                            line([pk, end], cool, 0.35 * bright, 1.0)
                # gold veins: random walks down the slope
                if layer < 2:
                    for _ in range(2 if layer == 0 else 1):
                        i = rng.randrange(n // 4, n - 10)
                        x, y = ridge[i].x(), ridge[i].y() + rng.uniform(6, 24)
                        pts = [QPointF(x, y)]
                        dx, dy = side * rng.uniform(0.6, 1.2), rng.uniform(0.5, 1.0)
                        for _ in range(rng.randint(12, 24)):
                            dx += rng.uniform(-0.35, 0.35)
                            dy = max(0.15, dy + rng.uniform(-0.25, 0.25))
                            norm = math.hypot(dx, dy)
                            x += dx / norm * 6
                            y += dy / norm * 6
                            if y > H or x < -10 or x > W + 10:
                                break
                            pts.append(QPointF(x, y))
                        line(pts, GOLD, 0.20, 5.0)
                        line(pts, GOLD, 0.65, 1.2)
        # a gold seam along the bottom-left, as in a lit valley
        seam = []
        x, y = 0.0, H * 0.86
        while x < cx - 0.30 * F:
            seam.append(QPointF(x, y))
            x += rng.uniform(8, 18)
            y += rng.uniform(-5, 4)
        line(seam, GOLD, 0.18, 5.0)
        line(seam, GOLD, 0.55, 1.2)

        # 4) silhouette: knock the mountains out from behind the body
        head = [self._head_pt(2 * math.pi * i / 120) for i in range(121)]
        sh_r = self._shoulder(0.0)                 # neck -> outer edge, right side
        sil = QPainterPath(P(-0.40, 1.06))
        for u, vv in reversed(sh_r):               # left side, outer edge -> neck
            sil.lineTo(P(-u, vv))
        sil.lineTo(P(-0.072, 0.47))
        sil.lineTo(P(0.072, 0.47))
        for u, vv in sh_r:                         # right side, neck -> outer edge
            sil.lineTo(P(u, vv))
        sil.lineTo(P(0.40, 1.06))
        sil.closeSubpath()
        hp = QPainterPath(P(*head[0]))
        for u, vv in head[1:]:
            hp.lineTo(P(u, vv))
        hp.closeSubpath()
        sil = sil.united(hp)

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(0, 0, 0, 235))
        p.drawPath(sil)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        # 5) body contours: nested shoulder arcs
        for k in range(0, 13):
            kk = k * 0.068
            arc_r = self._shoulder(kk)
            if k == 0:
                for sgn in (-1, 1):
                    side_pts = [P(sgn * 0.068, 0.49)] + [P(sgn * u, vv) for u, vv in arc_r]
                    line(side_pts, cool, 0.22, 7.0)
                    line(side_pts, white, 0.95, 1.6)
                continue
            arc = [P(-u, vv) for u, vv in reversed(arc_r)] + [P(u, vv) for u, vv in arc_r]
            line(arc, cool, max(0.18, 0.62 - k * 0.03), 1.1)

        # neck: contour lines and the outline
        for j, u0 in enumerate((-0.068, -0.046, -0.024, 0.024, 0.046, 0.068)):
            pts = []
            for i in range(11):
                vv = 0.48 + i * 0.010
                flare = max(0.0, (vv - 0.545)) * 1.8
                pts.append(P(u0 * (1 + flare * 5), vv))
            line(pts, cool, 0.85 if abs(u0) > 0.06 else 0.35, 1.3 if abs(u0) > 0.06 else 0.9)

        # head: halo particles, densest at the crown
        for _ in range(int(F * 7.5)):
            th = rng.uniform(0, 2 * math.pi)
            crown = max(0.0, math.cos(th)) ** 2
            if math.cos(th) < -0.55:        # nothing below the jaw
                continue
            u, vv = self._head_pt(th)
            nx, ny = math.sin(th), -math.cos(th)
            d = rng.expovariate(1.0 / (0.012 + 0.035 * crown))
            a = 0.85 * math.exp(-d / (0.02 + 0.05 * crown)) + 0.05
            dot(cx + (u + nx * d) * F + rng.uniform(-1, 1),
                top + (vv + ny * d) * F,
                white if rng.random() < 0.3 else cool, a,
                2.2 if rng.random() < 0.06 else 1.4)
        # shoulder edge spray
        for _ in range(int(F * 1.6)):
            i = rng.randrange(1, len(sh_r))
            (u0, v0), (u1, v1) = sh_r[i - 1], sh_r[i]
            t = rng.random()
            u, vv = u0 + (u1 - u0) * t, v0 + (v1 - v0) * t
            nl = math.hypot(v1 - v0, u1 - u0) or 1
            nx, ny = (v0 - v1) / nl, (u1 - u0) / nl          # outward normal
            d = rng.gauss(0, 0.010)
            sgn = rng.choice((-1, 1))
            dot(cx + sgn * (u + nx * d) * F, top + (vv + ny * d) * F, cool,
                0.55 * math.exp(-abs(d) / 0.012) + 0.05, 1.3)
        flush_dots()

        # head outline and edge contours
        for k, sc in enumerate((1.0, 0.968, 0.938)):
            ring = [P(*self._head_pt(2 * math.pi * i / 160, sc)) for i in range(161)]
            if k == 0:
                line(ring, cool, 0.28, 8.0)
                line(ring, white, 1.0, 2.0)
            else:
                line(ring, cool, 0.55 - k * 0.1, 1.1)
        p.end()

        # 6) bloom: two blurred copies of the emissive layer, added back
        out = QPainter(img)
        out.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        out.drawImage(0, 0, em)
        for div, op in ((4, 0.85), (12, 0.75)):
            small = em.scaled(max(1, W // div), max(1, H // div),
                              Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
            big = small.scaled(W, H, Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            out.setOpacity(op)
            out.drawImage(0, 0, big)
        out.end()

        # geometry the animated parts need
        tw = []
        for _ in range(320):
            if rng.random() < 0.55:
                th = rng.uniform(-1.9, 1.9)
                u, vv = self._head_pt(th)
                nx, ny = math.sin(th), -math.cos(th)
                d = rng.expovariate(1 / 0.03)
                tw.append((cx + (u + nx * d) * F, top + (vv + ny * d) * F,
                           rng.uniform(0, 6.28), rng.uniform(0.6, 2.2)))
            else:
                side = rng.choice((-1, 1))
                x = cx + side * rng.uniform(0.25 * F, W / 2)
                tw.append((x, rng.uniform(H * 0.35, H * 0.9),
                           rng.uniform(0, 6.28), rng.uniform(0.5, 1.6)))
        veins = []
        for j in range(4):
            x, y = rng.uniform(-0.03, 0.03), 0.50
            pts = [(x, y)]
            while y < 0.70:
                x += rng.uniform(-0.010, 0.010) - x * 0.15
                y += rng.uniform(0.010, 0.022)
                pts.append((x, y))
            veins.append(pts)
        # face rows: the shape is static, only ripple and drift move per frame
        rows = []
        n_rows = 26
        for k in range(n_rows):
            v0 = 0.125 + k * (0.385 / n_rows)
            th = math.acos(max(-1.0, min(1.0, (0.30 - v0) / 0.222)))
            w = abs(self._head_pt(th)[0]) * 0.90
            if w < 0.02:
                continue
            us = [-w + 2 * w * i / 40 for i in range(41)]
            base = [v0 + self._relief(u, v0) + 0.010 * (u / w) ** 2 * (1 if v0 > 0.33 else -0.6)
                    for u in us]
            rows.append((k, us, base, math.exp(-((v0 - 0.33) ** 2) / 0.011)))
        self._geo = dict(F=F, cx=cx, top=top, tw=tw, veins=veins, cool=cool, white=white,
                         rows=rows, fx=cx - 0.18 * F, fy=top + 0.06 * F,
                         fw=int(0.36 * F) + 2, fh=int(0.50 * F) + 2)
        self._face_img = None
        return img

    @staticmethod
    def _relief(u, v):
        """Face relief that bends the ridge lines: eye sockets, nose, mouth."""
        d = 0.0
        for ex in (-0.058, 0.058):
            d -= 0.011 * math.exp(-((u - ex) ** 2 + (v - 0.292) ** 2) / 0.0007)
        d += 0.016 * math.exp(-(u ** 2) / 0.0006) * math.exp(-((v - 0.36) ** 2) / 0.003)
        d += 0.007 * math.exp(-(u ** 2) / 0.002) * math.exp(-((v - 0.44) ** 2) / 0.0008)
        return d

    def _render_face(self, ph: float, amp: float, live: bool, muted: bool) -> QImage:
        g = self._geo
        F, cool = g["F"], g["cool"]
        img = QImage(g["fw"], g["fh"], QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(Qt.GlobalColor.transparent)
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        glows = []
        for k, us, base, warm0 in g["rows"]:
            path = None
            first = last = None
            for u, b in zip(us, base):
                wave = (amp * 0.006 * math.sin(u * 70 - ph * 9 + k * 0.6)) if live else 0.0
                drift = 0.0015 * math.sin(u * 40 + ph * 1.3 + k)
                q = QPointF((0.18 + u) * F, (b + wave + drift - 0.06) * F)
                if path is None:
                    path, first = QPainterPath(q), q
                else:
                    path.lineTo(q)
                last = q
            warmth = 0.0 if muted else warm0
            grad = QLinearGradient(first, last)
            edge = _with_alpha(cool, 0.75)
            grad.setColorAt(0.0, edge)
            grad.setColorAt(0.25, _with_alpha(_mix(cool, WARM, warmth * 0.6), 0.85))
            grad.setColorAt(0.5, _with_alpha(_mix(cool, WARM, warmth), 0.95))
            grad.setColorAt(0.75, _with_alpha(_mix(cool, WARM, warmth * 0.6), 0.85))
            grad.setColorAt(1.0, edge)
            glow = QLinearGradient(first, last)
            glow.setColorAt(0.0, _with_alpha(cool, 0.15))
            glow.setColorAt(0.5, _with_alpha(_mix(cool, WARM, warmth), 0.35))
            glow.setColorAt(1.0, _with_alpha(cool, 0.15))
            glows.append((path, glow))
            p.setPen(QPen(QBrush(grad), 1.1))
            p.drawPath(path)
        p.end()
        # soft glow: the same lines, thick, at half resolution, scaled up and added
        half = QImage(max(1, g["fw"] // 2), max(1, g["fh"] // 2),
                      QImage.Format.Format_ARGB32_Premultiplied)
        half.fill(Qt.GlobalColor.transparent)
        hp = QPainter(half)
        hp.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        hp.scale(0.5, 0.5)
        for path, glow in glows:
            hp.setPen(QPen(QBrush(glow), 5.0))
            hp.drawPath(path)
        hp.end()
        out = QPainter(img)
        out.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        out.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        out.drawImage(QRectF(0, 0, g["fw"], g["fh"]), half)
        out.end()
        return img

    # ── per frame ─────────────────────────────────────────────────────────────
    def paint(self, p: QPainter, W: int, H: int, *, dt: float, amp: float,
              state: str, speaking: bool, muted: bool, main: QColor,
              status: str, status_col: QColor):
        W, H = int(W), int(H)
        if W < 40 or H < 40:
            return
        key = (W, H, main.name())
        now = time.time()
        if self._img is None or (key != self._key and now - self._built_at > self.REBUILD_EVERY):
            self._img = self._build(W, H, main)
            self._key, self._built_at = key, now
        if key == self._key:
            p.drawImage(0, 0, self._img)
        else:   # mid-resize: stretch the last frame until the size settles
            p.drawImage(QRectF(0, 0, W, H), self._img)
            return

        g = self._geo
        F, cx, top = g["F"], g["cx"], g["top"]
        cool, white = g["cool"], g["white"]
        thinking = state in ("THINKING", "PROCESSING")
        live = (speaking or amp > 0.03) and not muted
        rate = 0.35 + (1.4 if thinking else 0.0) + (0.8 if speaking else 0.0)
        self._phase += max(0.0, min(0.1, dt)) * rate
        ph = self._phase
        breath = 0.5 + 0.5 * math.sin(ph * 1.7)

        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)

        def P(u, v):
            return QPointF(cx + u * F, top + v * F)

        # warm core
        fc = P(0.0, 0.335)
        gr = F * (0.15 + 0.03 * amp)
        core = 0.55 + 0.15 * breath + (0.45 * amp if live else 0.0)
        rg = QRadialGradient(fc, gr)
        rg.setColorAt(0.0, _with_alpha(WARM, min(1.0, core)))
        rg.setColorAt(0.55, _with_alpha(WARM, min(1.0, core * 0.45)))
        rg.setColorAt(1.0, _with_alpha(WARM, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(rg))
        p.drawEllipse(QRectF(fc.x() - gr, fc.y() - gr * 1.25, gr * 2, gr * 2.5))
        p.setBrush(Qt.BrushStyle.NoBrush)

        # face: ridge lines bent around eyes, nose and mouth, warm in the middle.
        # Redrawn every frame while there is sound (they ripple with it), and
        # five times a second otherwise, when all they do is drift slowly.
        if (self._face_img is None or live or self._face_live
                or muted != self._face_muted or now - self._face_t > 0.2):
            self._face_img = self._render_face(ph, amp, live, muted)
            self._face_t, self._face_live, self._face_muted = now, live, muted
        p.drawImage(QPointF(g["fx"], g["fy"]), self._face_img)

        # neck veins: pulse while thinking or speaking
        vein_a = 0.25 + (0.6 * (0.5 + 0.5 * math.sin(ph * 6)) if thinking else 0.0) \
                      + (0.5 * amp if speaking else 0.0)
        for vein in g["veins"]:
            path = QPainterPath(P(*vein[0]))
            for u, v in vein[1:]:
                path.lineTo(P(u, v))
            p.setPen(QPen(_with_alpha(GOLD, min(1, vein_a) * 0.35), 3.5))
            p.drawPath(path)
            p.setPen(QPen(_with_alpha(GOLD, min(1, vein_a)), 1.1))
            p.drawPath(path)

        # chest light
        cc = P(0.0, 0.94)
        cr = F * (0.035 + 0.02 * breath + (0.03 * amp if live else 0))
        rg2 = QRadialGradient(cc, cr)
        rg2.setColorAt(0.0, _with_alpha(white, 0.85))
        rg2.setColorAt(0.5, _with_alpha(cool, 0.35))
        rg2.setColorAt(1.0, _with_alpha(cool, 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(rg2))
        p.drawEllipse(QRectF(cc.x() - cr, cc.y() - cr, cr * 2, cr * 2))

        # twinkles
        p.setBrush(Qt.BrushStyle.NoBrush)
        levels = ([], [], [], [])
        for x, y, phs, spd in g["tw"]:
            levels[int((math.sin(now * spd + phs) * 0.5 + 0.5) * 3.999)].append(QPointF(x, y))
        for lvl, pts in enumerate(levels):
            pen = QPen(_with_alpha(white, 0.1 + lvl * 0.28), 1.6 + lvl * 0.5)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            p.drawPoints(pts)

        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if muted:
            p.fillRect(QRectF(0, 0, W, H), QColor(0, 0, 0, 120))

        # status, top right of the head
        f = QFont("Courier New", max(8, int(F * 0.016)), QFont.Weight.Bold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.0)
        p.setFont(f)
        p.setPen(QPen(status_col, 1))
        sx, sy = cx + 0.20 * F, top + 0.16 * F
        p.drawText(QRectF(sx, sy, W - sx - 8, 20),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, status)
        p.restore()
