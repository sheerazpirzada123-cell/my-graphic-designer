"""Vector PDF renderer (CMYK) built on ReportLab.

* All colours are written as DeviceCMYK operators (k / K), never RGB.
* All shapes are true vector paths. Text is converted to outlines when the font is a TTF
  (so print shops need no fonts), otherwise it stays as embedded live text.
* Real-world page size (mm/cm/in/ft...), optional bleed + crop marks, optional 1:N scale.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from reportlab.lib.colors import CMYKColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas
from reportlab.pdfgen.canvas import FILL_EVEN_ODD, FILL_NON_ZERO

from .colors import NAMED
from .fonts import has_arabic_script, resolve_font, shape_rtl
from .pathparse import parse_path
from .spec import normalize_spec, page_geometry
from .units import from_pt, to_pt

try:  # optional: text -> outlines
    from fontTools.pens.basePen import BasePen
    from fontTools.ttLib import TTFont as FTFont

    HAVE_FONTTOOLS = True
except ImportError:  # pragma: no cover
    HAVE_FONTTOOLS = False

_CAPS = {"butt": 0, "round": 1, "square": 2}
_JOINS = {"miter": 0, "round": 1, "bevel": 2}


@dataclass
class RenderReport:
    path: str
    page_width_pt: float
    page_height_pt: float
    trim_width: float
    trim_height: float
    unit: str
    scale: float
    auto_scaled: bool
    warnings: list[str] = field(default_factory=list)
    live_fonts: list[str] = field(default_factory=list)  # fonts left as live (non-outlined) text

    def summary(self) -> str:
        s = f"{self.trim_width:g} x {self.trim_height:g} {self.unit}"
        if self.scale != 1:
            s += f" (output at 1:{round(1 / self.scale)} scale)"
        return s


def _u(col: dict) -> tuple[float, float, float, float]:
    return (col["c"] / 100.0, col["m"] / 100.0, col["y"] / 100.0, col["k"] / 100.0)


def _rl_color(col: dict) -> CMYKColor:
    return CMYKColor(*_u(col))


def _is_gradient(p) -> bool:
    return isinstance(p, dict) and p.get("type") in ("linear", "radial")


class _Ctx:
    def __init__(self, c, g, assets_dir, warnings):
        self.c = c
        self.g = g
        self.k = g["u2pt"]  # points per design unit
        self.assets_dir = assets_dir
        self.warnings = warnings
        self.live_fonts: set[str] = set()

    # design (y-down, spec unit) -> PDF real points (y-up)
    def X(self, x: float) -> float:
        return self.g["offset"] + x * self.k

    def Y(self, y: float) -> float:
        return self.g["total_h"] - (self.g["offset"] + y * self.k)

    def L(self, v: float) -> float:
        return v * self.k

    def warn(self, msg: str) -> None:
        if msg not in self.warnings:
            self.warnings.append(msg)


# --------------------------------------------------------------------------- painting
def _apply_stroke_style(c, el) -> bool:
    stroke = el.get("stroke")
    sw = el.get("stroke_width", 1.0)
    if not stroke or sw <= 0:
        return False
    c.setStrokeColorCMYK(*_u(stroke))
    c.setLineWidth(sw)
    c.setLineCap(_CAPS[el.get("cap", "butt")])
    c.setLineJoin(_JOINS[el.get("join", "miter")])
    if el.get("dash"):
        c.setDash(el["dash"])
    return True


def _draw_gradient(ctx: _Ctx, grad: dict) -> None:
    c = ctx.c
    colors = [_rl_color(s["color"]) for s in grad["stops"]]
    pos = [s["offset"] for s in grad["stops"]]
    if grad["type"] == "linear":
        c.linearGradient(ctx.X(grad["x1"]), ctx.Y(grad["y1"]), ctx.X(grad["x2"]), ctx.Y(grad["y2"]),
                         colors, pos, extend=True)
    else:
        c.radialGradient(ctx.X(grad["cx"]), ctx.Y(grad["cy"]), ctx.L(grad["r"]), colors, pos, extend=True)


def _paint_path(ctx: _Ctx, p, el) -> None:
    c = ctx.c
    fill = el.get("fill")
    mode = FILL_EVEN_ODD if el.get("fill_rule") == "evenodd" else FILL_NON_ZERO
    has_stroke = _apply_stroke_style(c, el)
    if _is_gradient(fill):
        c.saveState()
        c.clipPath(p, stroke=0, fill=0, fillMode=mode)
        _draw_gradient(ctx, fill)
        c.restoreState()
        if has_stroke:
            c.drawPath(p, stroke=1, fill=0)
        return
    if fill:
        c.setFillColorCMYK(*_u(fill))
    if fill or has_stroke:
        c.drawPath(p, stroke=int(has_stroke), fill=int(bool(fill)), fillMode=mode)


def _bbox_center(points: list[tuple[float, float]]) -> tuple[float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def _begin(ctx: _Ctx, el, pivot_design: tuple[float, float]) -> None:
    c = ctx.c
    c.saveState()
    if el.get("opacity") is not None and el["opacity"] < 1:
        c.setFillAlpha(el["opacity"])
        c.setStrokeAlpha(el["opacity"])
    if el.get("rotate"):
        px, py = ctx.X(pivot_design[0]), ctx.Y(pivot_design[1])
        c.translate(px, py)
        c.rotate(-el["rotate"])  # spec: clockwise positive (y-down); PDF: counter-clockwise positive
        c.translate(-px, -py)


def _draw_rect(ctx, el):
    _begin(ctx, el, (el["x"] + el["width"] / 2, el["y"] + el["height"] / 2))
    p = ctx.c.beginPath()
    x, y = ctx.X(el["x"]), ctx.Y(el["y"] + el["height"])
    w, h = ctx.L(el["width"]), ctx.L(el["height"])
    r = min(ctx.L(el["radius"]), w / 2, h / 2)
    if r > 0:
        p.roundRect(x, y, w, h, r)
    else:
        p.rect(x, y, w, h)
    _paint_path(ctx, p, el)
    ctx.c.restoreState()


def _draw_ellipse(ctx, el):
    _begin(ctx, el, (el["cx"], el["cy"]))
    p = ctx.c.beginPath()
    p.ellipse(ctx.X(el["cx"] - el["rx"]), ctx.Y(el["cy"] + el["ry"]), ctx.L(2 * el["rx"]), ctx.L(2 * el["ry"]))
    _paint_path(ctx, p, el)
    ctx.c.restoreState()


def _draw_line(ctx, el):
    _begin(ctx, el, ((el["x1"] + el["x2"]) / 2, (el["y1"] + el["y2"]) / 2))
    p = ctx.c.beginPath()
    p.moveTo(ctx.X(el["x1"]), ctx.Y(el["y1"]))
    p.lineTo(ctx.X(el["x2"]), ctx.Y(el["y2"]))
    e = dict(el)
    e["fill"] = None
    _paint_path(ctx, p, e)
    ctx.c.restoreState()


def _draw_poly(ctx, el):
    pts = el["points"]
    _begin(ctx, el, _bbox_center([tuple(p) for p in pts]))
    p = ctx.c.beginPath()
    p.moveTo(ctx.X(pts[0][0]), ctx.Y(pts[0][1]))
    for x, y in pts[1:]:
        p.lineTo(ctx.X(x), ctx.Y(y))
    if el["type"] == "polygon":
        p.close()
    _paint_path(ctx, p, el)
    ctx.c.restoreState()


def _draw_path(ctx, el):
    segs = parse_path(el["d"])
    pts = []
    for s in segs:
        if s[0] in ("M", "L"):
            pts.append((s[1], s[2]))
        elif s[0] == "C":
            pts.extend([(s[1], s[2]), (s[3], s[4]), (s[5], s[6])])
    _begin(ctx, el, _bbox_center(pts) if pts else (0, 0))
    p = ctx.c.beginPath()
    for s in segs:
        if s[0] == "M":
            p.moveTo(ctx.X(s[1]), ctx.Y(s[2]))
        elif s[0] == "L":
            p.lineTo(ctx.X(s[1]), ctx.Y(s[2]))
        elif s[0] == "C":
            p.curveTo(ctx.X(s[1]), ctx.Y(s[2]), ctx.X(s[3]), ctx.Y(s[4]), ctx.X(s[5]), ctx.Y(s[6]))
        else:
            p.close()
    _paint_path(ctx, p, el)
    ctx.c.restoreState()


# --------------------------------------------------------------------------- text
@lru_cache(maxsize=32)
def _ft_font(path: str):
    return FTFont(path, lazy=True)


def _outline_ok(path: str, text: str) -> bool:
    if not HAVE_FONTTOOLS:
        return False
    cmap = _ft_font(path).getBestCmap() or {}
    return all(ord(ch) in cmap for ch in text)


def _wrap(text: str, font: str, size: float, max_w: float, spacing: float) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        words = para.split(" ")
        cur = ""
        for w in words:
            trial = w if not cur else cur + " " + w
            width = pdfmetrics.stringWidth(trial, font, size) + spacing * max(len(trial) - 1, 0)
            if cur and width > max_w:
                lines.append(cur)
                cur = w
            else:
                cur = trial
        lines.append(cur)
    return lines


def _line_width(line: str, font: str, size: float, spacing: float) -> float:
    return pdfmetrics.stringWidth(line, font, size) + spacing * max(len(line) - 1, 0)


def _draw_text(ctx: _Ctx, el):
    c = ctx.c
    ref, warn = resolve_font(el["font"], el["bold"], el["italic"])
    if warn:
        ctx.warn(warn)
    size = el["size"]
    spacing = el["letter_spacing"]
    text = el["text"]
    rtl = el["rtl"] or has_arabic_script(text)

    max_w = ctx.L(el["max_width"]) if el.get("max_width") else None
    if max_w:
        lines = _wrap(text, ref.name, size, max_w, spacing)
    else:
        lines = text.split("\n")

    _begin(ctx, el, (el["x"], el["y"]))
    x0, y0 = ctx.X(el["x"]), ctx.Y(el["y"])
    lead = size * el["line_height"]
    fill, stroke = el.get("fill"), el.get("stroke")

    for i, raw in enumerate(lines):
        line = shape_rtl(raw) if rtl else raw
        width = _line_width(line, ref.name, size, spacing)
        if el["align"] == "center":
            lx = x0 - width / 2
        elif el["align"] == "right":
            lx = x0 - width
        else:
            lx = x0
        ly = y0 - i * lead
        if not line.strip():
            continue

        if el["outline"] and ref.path and not rtl and _outline_ok(ref.path, line):
            _draw_outlined_line(ctx, ref.path, line, size, lx, ly, spacing, fill, stroke, el["stroke_width"])
        else:
            if ref.embedded or ref.path is None:
                ctx.live_fonts.add(ref.name)
            t = c.beginText()
            t.setTextOrigin(lx, ly)
            t.setFont(ref.name, size)
            t.setCharSpace(spacing)
            if fill:
                t.setFillColorCMYK(*_u(fill))
            if stroke and el["stroke_width"] > 0:
                c.setStrokeColorCMYK(*_u(stroke))
                c.setLineWidth(el["stroke_width"])
                t.setTextRenderMode(2 if fill else 1)
            t.textOut(line)
            c.drawText(t)
    c.restoreState()


def _draw_outlined_line(ctx, path, line, size, lx, ly, spacing, fill, stroke, stroke_w):
    c = ctx.c
    font = _ft_font(path)
    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    hmtx = font["hmtx"]
    upm = font["head"].unitsPerEm
    s = size / upm
    p = c.beginPath()

    class Pen(BasePen):
        def __init__(self, glyph_set, ox):
            super().__init__(glyph_set)
            self.ox = ox

        def _moveTo(self, pt):
            p.moveTo(self.ox + pt[0] * s, ly + pt[1] * s)

        def _lineTo(self, pt):
            p.lineTo(self.ox + pt[0] * s, ly + pt[1] * s)

        def _curveToOne(self, a, b, d):
            p.curveTo(self.ox + a[0] * s, ly + a[1] * s, self.ox + b[0] * s, ly + b[1] * s,
                      self.ox + d[0] * s, ly + d[1] * s)

        def _closePath(self):
            p.close()

        def _endPath(self):
            pass

    pen_x = lx
    for ch in line:
        name = cmap[ord(ch)]
        gs[name].draw(Pen(gs, pen_x))
        pen_x += hmtx[name][0] * s + spacing

    has_stroke = bool(stroke) and stroke_w > 0
    if has_stroke:
        c.setStrokeColorCMYK(*_u(stroke))
        c.setLineWidth(stroke_w)
    if fill:
        c.setFillColorCMYK(*_u(fill))
    if fill or has_stroke:
        c.drawPath(p, stroke=int(has_stroke), fill=int(bool(fill)), fillMode=FILL_NON_ZERO)


# --------------------------------------------------------------------------- image
def _draw_image(ctx: _Ctx, el):
    if ctx.assets_dir is None:
        ctx.warn("Image skipped: no assets directory given.")
        return
    base = Path(ctx.assets_dir).resolve()
    target = (base / el["path"]).resolve()
    if base not in target.parents and target != base:
        ctx.warn(f"Image '{el['path']}' skipped: outside the assets folder.")
        return
    if not target.is_file():
        ctx.warn(f"Image '{el['path']}' not found.")
        return
    ctx.warn("Raster image embedded (RGB, not vector). Replace with vector art for best print quality.")
    _begin(ctx, el, (el["x"] + el["width"] / 2, el["y"] + el["height"] / 2))
    ctx.c.drawImage(str(target), ctx.X(el["x"]), ctx.Y(el["y"] + el["height"]),
                    ctx.L(el["width"]), ctx.L(el["height"]), mask="auto")
    ctx.c.restoreState()


_DRAW = {
    "rect": _draw_rect, "ellipse": _draw_ellipse, "line": _draw_line,
    "polygon": _draw_poly, "polyline": _draw_poly, "path": _draw_path,
    "text": _draw_text, "image": _draw_image,
}


# --------------------------------------------------------------------------- marks
def _crop_marks(ctx: _Ctx):
    c, g = ctx.c, ctx.g
    reg = NAMED["registration"].unit()
    c.saveState()
    c.setStrokeColorCMYK(*reg)
    c.setLineWidth(0.25)
    gap = to_pt(1, "mm")
    ln = max(g["slug"] - gap - to_pt(0.5, "mm"), to_pt(2, "mm"))
    L, B = g["offset"], g["offset"]
    R, T = L + g["trim_w"], B + g["trim_h"]
    for cx, cy, sx, sy in ((L, B, -1, -1), (R, B, 1, -1), (L, T, -1, 1), (R, T, 1, 1)):
        c.line(cx + sx * (g["bleed"] + gap), cy, cx + sx * (g["bleed"] + gap + ln), cy)
        c.line(cx, cy + sy * (g["bleed"] + gap), cx, cy + sy * (g["bleed"] + gap + ln))
    c.restoreState()


# --------------------------------------------------------------------------- entry point
def render_pdf(spec: dict, out_path: str | Path, assets_dir: str | Path | None = None) -> RenderReport:
    spec = normalize_spec(spec)
    g = page_geometry(spec)
    warnings: list[str] = []
    if g["auto_scaled"]:
        warnings.append(
            f"Design is larger than the PDF limit (200 in). Exported at 1:{round(1 / g['scale'])} scale; "
            "print at 100% x the scale factor, or scale up in your RIP software."
        )

    s = g["scale"]
    W, H = g["total_w"] * s, g["total_h"] * s
    c = rl_canvas.Canvas(str(out_path), pagesize=(W, H), pageCompression=1)
    c.setTitle(spec["title"])
    c.setCreator("designer-ai")
    c.setAuthor("designer-ai")

    off, tw, th, bl, sl = g["offset"], g["trim_w"], g["trim_h"], g["bleed"], g["slug"]
    c.setTrimBox((off * s, off * s, (off + tw) * s, (off + th) * s))
    c.setBleedBox((sl * s, sl * s, (sl + tw + 2 * bl) * s, (sl + th + 2 * bl) * s))

    c.scale(s, s)
    ctx = _Ctx(c, g, assets_dir, warnings)

    c.saveState()
    clip = c.beginPath()
    clip.rect(sl, sl, tw + 2 * bl, th + 2 * bl)  # everything is clipped to the bleed box
    c.clipPath(clip, stroke=0, fill=0)

    bg = spec.get("background")
    if bg:
        p = c.beginPath()
        p.rect(sl, sl, tw + 2 * bl, th + 2 * bl)
        _paint_path(ctx, p, {"fill": bg})

    for el in spec["elements"]:
        _DRAW[el["type"]](ctx, el)
    c.restoreState()

    if spec["crop_marks"]:
        _crop_marks(ctx)

    c.showPage()
    c.save()

    if ctx.live_fonts:
        warnings.append(
            "Live (non-outlined) text used font(s): " + ", ".join(sorted(ctx.live_fonts)) +
            ". TTF fonts are embedded; base-14 fonts (Helvetica/Times/Courier) are not. "
            "Convert text to outlines in your editor before sending to a printer if needed."
        )

    return RenderReport(
        path=str(out_path), page_width_pt=W, page_height_pt=H,
        trim_width=spec["size"]["width"], trim_height=spec["size"]["height"], unit=spec["size"]["unit"],
        scale=s, auto_scaled=g["auto_scaled"], warnings=warnings, live_fonts=sorted(ctx.live_fonts),
    )
