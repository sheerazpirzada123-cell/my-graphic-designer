"""SVG export (vector, editable in Illustrator / CorelDRAW / Inkscape / Figma).

SVG has no native CMYK, so colours are approximated in RGB here. Use the PDF for print.
Text is converted to outlines whenever the font is TTF, so the file looks right anywhere.
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from .colors import CMYK
from .fonts import has_arabic_script, resolve_font, shape_rtl
from .pathparse import parse_path
from .render_pdf import HAVE_FONTTOOLS, _ft_font, _outline_ok, _wrap
from .spec import normalize_spec, page_geometry
from .units import from_pt

if HAVE_FONTTOOLS:
    from fontTools.pens.basePen import BasePen


def _hex(col: dict) -> str:
    return CMYK(col["c"], col["m"], col["y"], col["k"]).to_hex()


def _f(v: float) -> str:
    return f"{v:.4f}".rstrip("0").rstrip(".")


def _pivot(el) -> tuple[float, float]:
    t = el["type"]
    if t == "rect" or t == "image":
        return el["x"] + el["width"] / 2, el["y"] + el["height"] / 2
    if t == "ellipse":
        return el["cx"], el["cy"]
    if t == "line":
        return (el["x1"] + el["x2"]) / 2, (el["y1"] + el["y2"]) / 2
    if t in ("polygon", "polyline"):
        xs = [p[0] for p in el["points"]]
        ys = [p[1] for p in el["points"]]
        return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    if t == "path":
        xs, ys = [], []
        for s in parse_path(el["d"]):
            vals = s[1:]
            xs.extend(vals[0::2])
            ys.extend(vals[1::2])
        return ((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2) if xs else (0, 0)
    return el.get("x", 0), el.get("y", 0)


class _Defs:
    def __init__(self):
        self.items: list[str] = []

    def gradient(self, g: dict) -> str:
        gid = f"g{len(self.items)}"
        stops = "".join(
            f'<stop offset="{_f(s["offset"])}" stop-color="{_hex(s["color"])}"/>' for s in g["stops"]
        )
        if g["type"] == "linear":
            self.items.append(
                f'<linearGradient id="{gid}" gradientUnits="userSpaceOnUse" x1="{_f(g["x1"])}" y1="{_f(g["y1"])}" '
                f'x2="{_f(g["x2"])}" y2="{_f(g["y2"])}">{stops}</linearGradient>'
            )
        else:
            self.items.append(
                f'<radialGradient id="{gid}" gradientUnits="userSpaceOnUse" cx="{_f(g["cx"])}" cy="{_f(g["cy"])}" '
                f'r="{_f(g["r"])}">{stops}</radialGradient>'
            )
        return gid


def _paint_attrs(el: dict, defs: _Defs, k: float) -> str:
    fill = el.get("fill")
    if fill is None:
        fa = 'fill="none"'
    elif fill.get("type") in ("linear", "radial"):
        fa = f'fill="url(#{defs.gradient(fill)})"'
    else:
        fa = f'fill="{_hex(fill)}"'
    stroke = el.get("stroke")
    sa = ""
    if stroke and el.get("stroke_width", 1) > 0:
        sa = f' stroke="{_hex(stroke)}" stroke-width="{_f(el["stroke_width"] / k)}"'
        if el.get("cap", "butt") != "butt":
            sa += f' stroke-linecap="{el["cap"]}"'
        if el.get("join", "miter") != "miter":
            sa += f' stroke-linejoin="{el["join"]}"'
        if el.get("dash"):
            sa += ' stroke-dasharray="' + " ".join(_f(d / k) for d in el["dash"]) + '"'
    return fa + sa


def _text_outline_d(path: str, line: str, size_pt: float, x: float, y: float, spacing_pt: float, k: float) -> str:
    font = _ft_font(path)
    cmap = font.getBestCmap()
    gs = font.getGlyphSet()
    hmtx = font["hmtx"]
    s = size_pt / font["head"].unitsPerEm / k  # glyph units -> design units
    adv_extra = spacing_pt / k
    parts: list[str] = []

    class Pen(BasePen):
        def __init__(self, glyph_set, ox):
            super().__init__(glyph_set)
            self.ox = ox

        def _pt(self, p):
            return f"{_f(self.ox + p[0] * s)} {_f(y - p[1] * s)}"

        def _moveTo(self, pt):
            parts.append("M" + self._pt(pt))

        def _lineTo(self, pt):
            parts.append("L" + self._pt(pt))

        def _curveToOne(self, a, b, d):
            parts.append("C" + " ".join(self._pt(p) for p in (a, b, d)))

        def _closePath(self):
            parts.append("Z")

        def _endPath(self):
            pass

    pen_x = x
    for ch in line:
        name = cmap[ord(ch)]
        gs[name].draw(Pen(gs, pen_x))
        pen_x += hmtx[name][0] * s + adv_extra
    return "".join(parts)


def _text_el(el: dict, defs: _Defs, k: float) -> str:
    from reportlab.pdfbase import pdfmetrics

    ref, _ = resolve_font(el["font"], el["bold"], el["italic"])
    size, spacing = el["size"], el["letter_spacing"]
    rtl = el["rtl"] or has_arabic_script(el["text"])
    lines = _wrap(el["text"], ref.name, size, el["max_width"] * k, spacing) if el.get("max_width") else el["text"].split("\n")
    lead = size * el["line_height"] / k
    out: list[str] = []
    attrs = _paint_attrs({"fill": el.get("fill"), "stroke": el.get("stroke"), "stroke_width": el["stroke_width"]}, defs, k)
    for i, raw in enumerate(lines):
        line = shape_rtl(raw) if rtl else raw
        if not line.strip():
            continue
        width = (pdfmetrics.stringWidth(line, ref.name, size) + spacing * max(len(line) - 1, 0)) / k
        x = el["x"] - (width / 2 if el["align"] == "center" else width if el["align"] == "right" else 0)
        y = el["y"] + i * lead
        if el["outline"] and ref.path and not rtl and _outline_ok(ref.path, line):
            d = _text_outline_d(ref.path, line, size, x, y, spacing, k)
            out.append(f'<path d="{d}" {attrs}/>')
        else:
            fam = escape(el["font"], {'"': "&quot;"})
            weight = ' font-weight="bold"' if el["bold"] else ""
            style = ' font-style="italic"' if el["italic"] else ""
            anchor = {"left": "start", "center": "middle", "right": "end"}[el["align"]]
            out.append(
                f'<text x="{_f(el["x"])}" y="{_f(y)}" font-family="{fam}, Helvetica, Arial, sans-serif" '
                f'font-size="{_f(size / k)}"{weight}{style} text-anchor="{anchor}" {attrs}>{escape(line)}</text>'
            )
    return "".join(out)


def render_svg(spec: dict, out_path: str | Path) -> str:
    spec = normalize_spec(spec)
    g = page_geometry(spec)
    k, off = g["u2pt"], g["offset"]
    defs = _Defs()
    body: list[str] = []

    bg = spec.get("background")
    unit_w = g["trim_w"] / k
    unit_h = g["trim_h"] / k
    bleed_u = g["bleed"] / k
    if bg:
        body.append(
            f'<rect x="{_f(-bleed_u)}" y="{_f(-bleed_u)}" width="{_f(unit_w + 2 * bleed_u)}" '
            f'height="{_f(unit_h + 2 * bleed_u)}" {_paint_attrs({"fill": bg}, defs, k)}/>'
        )

    for el in spec["elements"]:
        t = el["type"]
        attrs = _paint_attrs(el, defs, k)
        tr = ""
        if el.get("rotate"):
            px, py = _pivot(el)
            tr = f' transform="rotate({_f(el["rotate"])} {_f(px)} {_f(py)})"'
        op = f' opacity="{_f(el["opacity"])}"' if el.get("opacity") is not None and el["opacity"] < 1 else ""
        extra = tr + op
        if t == "rect":
            r = min(el["radius"], el["width"] / 2, el["height"] / 2)
            rr = f' rx="{_f(r)}" ry="{_f(r)}"' if r > 0 else ""
            body.append(f'<rect x="{_f(el["x"])}" y="{_f(el["y"])}" width="{_f(el["width"])}" height="{_f(el["height"])}"{rr} {attrs}{extra}/>')
        elif t == "ellipse":
            body.append(f'<ellipse cx="{_f(el["cx"])}" cy="{_f(el["cy"])}" rx="{_f(el["rx"])}" ry="{_f(el["ry"])}" {attrs}{extra}/>')
        elif t == "line":
            body.append(f'<line x1="{_f(el["x1"])}" y1="{_f(el["y1"])}" x2="{_f(el["x2"])}" y2="{_f(el["y2"])}" {attrs}{extra}/>')
        elif t in ("polygon", "polyline"):
            pts = " ".join(f"{_f(x)},{_f(y)}" for x, y in el["points"])
            body.append(f'<{t} points="{pts}" {attrs}{extra}/>')
        elif t == "path":
            rule = ' fill-rule="evenodd"' if el.get("fill_rule") == "evenodd" else ""
            body.append(f'<path d="{escape(el["d"])}" {attrs}{rule}{extra}/>')
        elif t == "text":
            inner = _text_el(el, defs, k)
            if inner:
                body.append(f"<g{extra}>{inner}</g>" if extra else inner)
        # raster images are not exported to SVG

    # The SVG shows trim + bleed only (crop marks live in the PDF).
    view_w = g["trim_w"] + 2 * g["bleed"]
    view_h = g["trim_h"] + 2 * g["bleed"]
    unit_out = "mm" if g["unit"] in ("mm", "cm", "m") else "in"
    w_out = from_pt(view_w * g["scale"], unit_out)
    h_out = from_pt(view_h * g["scale"], unit_out)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_f(w_out)}{unit_out}" height="{_f(h_out)}{unit_out}" '
        f'viewBox="{_f(g["slug"])} {_f(g["slug"])} {_f(view_w)} {_f(view_h)}">\n'
        f"<title>{escape(spec['title'])}</title>\n"
        f"<defs>{''.join(defs.items)}"
        f'<clipPath id="bleed"><rect x="{_f(g["slug"])}" y="{_f(g["slug"])}" width="{_f(g["trim_w"] + 2 * g["bleed"])}" '
        f'height="{_f(g["trim_h"] + 2 * g["bleed"])}"/></clipPath></defs>\n'
        f'<g clip-path="url(#bleed)"><g transform="translate({_f(off)} {_f(off)}) scale({_f(k)})">\n'
        + "\n".join(body)
        + "\n</g></g>\n</svg>\n"
    )
    Path(out_path).write_text(svg, encoding="utf-8")
    return str(out_path)
