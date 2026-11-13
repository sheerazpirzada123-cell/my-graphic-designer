"""Design spec: validation + normalisation.

A spec is plain JSON (see schema.py for the documented format). `normalize_spec` returns a
cleaned deep copy in which every colour is an explicit CMYK object, so saved specs are
CMYK-only and fully reproducible.
"""
from __future__ import annotations

import copy
import math
from typing import Any

from .colors import CMYK, parse_color
from .pathparse import parse_path
from .units import MAX_PDF_SIDE_PT, normalize_unit, parse_size, to_pt

MAX_ELEMENTS = 3000
ELEMENT_TYPES = {"rect", "ellipse", "line", "polygon", "polyline", "path", "text", "image"}


class SpecError(ValueError):
    """Raised when a design spec is invalid. The message is written to be fed back to the model."""


def _num(v: Any, where: str, *, positive: bool = False, default: float | None = None) -> float:
    if v is None:
        if default is not None:
            return default
        raise SpecError(f"{where}: number required.")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise SpecError(f"{where}: '{v}' is not a number.")
    if not math.isfinite(f):
        raise SpecError(f"{where}: must be finite.")
    if positive and f <= 0:
        raise SpecError(f"{where}: must be > 0.")
    return f


def _cmyk_dict(c: CMYK) -> dict:
    return {"c": round(c.c, 2), "m": round(c.m, 2), "y": round(c.y, 2), "k": round(c.k, 2)}


def _paint(v: Any, where: str, size_unit: str | None = None) -> Any:
    """Normalise a fill/stroke: CMYK dict, gradient dict, or None."""
    if isinstance(v, dict) and v.get("type") in ("linear", "radial"):
        g: dict[str, Any] = {"type": v["type"]}
        if v["type"] == "linear":
            for k in ("x1", "y1", "x2", "y2"):
                g[k] = _num(v.get(k), f"{where}.{k}")
        else:
            g["cx"] = _num(v.get("cx"), f"{where}.cx")
            g["cy"] = _num(v.get("cy"), f"{where}.cy")
            g["r"] = _num(v.get("r"), f"{where}.r", positive=True)
        stops = v.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            raise SpecError(f"{where}: gradient needs at least 2 stops.")
        out = []
        for i, s in enumerate(stops):
            if not isinstance(s, dict) or "color" not in s:
                raise SpecError(f"{where}.stops[{i}]: needs offset and color.")
            col = parse_color(s["color"])
            if col is None:
                raise SpecError(f"{where}.stops[{i}]: colour cannot be 'none'.")
            off = min(1.0, max(0.0, _num(s.get("offset"), f"{where}.stops[{i}].offset")))
            out.append({"offset": off, "color": _cmyk_dict(col)})
        out.sort(key=lambda s: s["offset"])
        g["stops"] = out
        return g
    try:
        col = parse_color(v)
    except ValueError as e:
        raise SpecError(f"{where}: {e}")
    return None if col is None else _cmyk_dict(col)


def _common(el: dict, i: int, out: dict) -> None:
    w = f"elements[{i}]"
    if "name" in el:
        out["name"] = str(el["name"])
    if "opacity" in el and el["opacity"] is not None:
        out["opacity"] = min(1.0, max(0.0, _num(el["opacity"], f"{w}.opacity")))
    if "rotate" in el and el["rotate"] is not None:
        out["rotate"] = _num(el["rotate"], f"{w}.rotate")


def _stroke_props(el: dict, i: int, out: dict, default_stroke_width: float = 1.0) -> None:
    w = f"elements[{i}]"
    out["stroke"] = _paint(el.get("stroke"), f"{w}.stroke")
    out["stroke_width"] = _num(el.get("stroke_width"), f"{w}.stroke_width", default=default_stroke_width)
    if out["stroke_width"] < 0:
        raise SpecError(f"{w}.stroke_width must be >= 0.")
    cap = el.get("cap", "butt")
    join = el.get("join", "miter")
    if cap not in ("butt", "round", "square"):
        raise SpecError(f"{w}.cap must be butt, round or square.")
    if join not in ("miter", "round", "bevel"):
        raise SpecError(f"{w}.join must be miter, round or bevel.")
    out["cap"], out["join"] = cap, join
    dash = el.get("dash")
    if dash:
        out["dash"] = [_num(d, f"{w}.dash", positive=True) for d in dash][:6]


def _element(el: dict, i: int) -> dict:
    w = f"elements[{i}]"
    if not isinstance(el, dict):
        raise SpecError(f"{w}: must be an object.")
    t = el.get("type")
    if t not in ELEMENT_TYPES:
        raise SpecError(f"{w}: unknown type '{t}'. Allowed: {', '.join(sorted(ELEMENT_TYPES))}.")
    out: dict[str, Any] = {"type": t}
    _common(el, i, out)

    if t == "rect":
        for k in ("x", "y"):
            out[k] = _num(el.get(k), f"{w}.{k}")
        for k in ("width", "height"):
            out[k] = _num(el.get(k), f"{w}.{k}", positive=True)
        out["radius"] = max(0.0, _num(el.get("radius"), f"{w}.radius", default=0.0))
        out["fill"] = _paint(el.get("fill"), f"{w}.fill")
        _stroke_props(el, i, out)
    elif t == "ellipse":
        out["cx"] = _num(el.get("cx"), f"{w}.cx")
        out["cy"] = _num(el.get("cy"), f"{w}.cy")
        if el.get("r") is not None:
            out["rx"] = out["ry"] = _num(el["r"], f"{w}.r", positive=True)
        else:
            out["rx"] = _num(el.get("rx"), f"{w}.rx", positive=True)
            out["ry"] = _num(el.get("ry"), f"{w}.ry", positive=True)
        out["fill"] = _paint(el.get("fill"), f"{w}.fill")
        _stroke_props(el, i, out)
    elif t == "line":
        for k in ("x1", "y1", "x2", "y2"):
            out[k] = _num(el.get(k), f"{w}.{k}")
        _stroke_props(el, i, out)
        if out["stroke"] is None:
            raise SpecError(f"{w}: a line needs a stroke colour.")
    elif t in ("polygon", "polyline"):
        pts = el.get("points")
        if not isinstance(pts, list) or len(pts) < 2:
            raise SpecError(f"{w}.points: list of at least 2 [x, y] pairs required.")
        out["points"] = []
        for j, p in enumerate(pts):
            if not (isinstance(p, (list, tuple)) and len(p) == 2):
                raise SpecError(f"{w}.points[{j}]: must be [x, y].")
            out["points"].append([_num(p[0], f"{w}.points[{j}][0]"), _num(p[1], f"{w}.points[{j}][1]")])
        out["fill"] = _paint(el.get("fill"), f"{w}.fill")
        _stroke_props(el, i, out)
    elif t == "path":
        d = el.get("d")
        if not isinstance(d, str) or not d.strip():
            raise SpecError(f"{w}.d: SVG path data string required.")
        try:
            parse_path(d)
        except ValueError as e:
            raise SpecError(f"{w}.d: {e}")
        out["d"] = d
        out["fill"] = _paint(el.get("fill"), f"{w}.fill")
        _stroke_props(el, i, out)
        out["fill_rule"] = el.get("fill_rule", "nonzero")
        if out["fill_rule"] not in ("nonzero", "evenodd"):
            raise SpecError(f"{w}.fill_rule must be nonzero or evenodd.")
    elif t == "text":
        text = el.get("text")
        if not isinstance(text, str) or text == "":
            raise SpecError(f"{w}.text: non-empty string required.")
        out["text"] = text
        out["x"] = _num(el.get("x"), f"{w}.x")
        out["y"] = _num(el.get("y"), f"{w}.y")
        size_val = _num(el.get("size"), f"{w}.size", positive=True)
        try:
            # "size" is in points unless "size_unit" says otherwise (e.g. "in", "mm", "ft" for banners)
            out["size"] = to_pt(size_val, str(el.get("size_unit") or "pt"))
        except ValueError as e:
            raise SpecError(f"{w}.size_unit: {e}")
        out["font"] = str(el.get("font", "Helvetica"))
        out["bold"] = bool(el.get("bold", False))
        out["italic"] = bool(el.get("italic", False))
        out["fill"] = _paint(el.get("fill", {"c": 0, "m": 0, "y": 0, "k": 100}), f"{w}.fill")
        out["stroke"] = _paint(el.get("stroke"), f"{w}.stroke")
        out["stroke_width"] = _num(el.get("stroke_width"), f"{w}.stroke_width", default=0.5)
        align = el.get("align", "left")
        if align not in ("left", "center", "right"):
            raise SpecError(f"{w}.align must be left, center or right.")
        out["align"] = align
        out["line_height"] = _num(el.get("line_height"), f"{w}.line_height", default=1.2, positive=True)
        out["letter_spacing"] = _num(el.get("letter_spacing"), f"{w}.letter_spacing", default=0.0)
        if el.get("max_width") is not None:
            out["max_width"] = _num(el["max_width"], f"{w}.max_width", positive=True)
        out["rtl"] = bool(el.get("rtl", False))
        out["outline"] = bool(el.get("outline", True))
    elif t == "image":
        if not isinstance(el.get("path"), str):
            raise SpecError(f"{w}.path: file path of the image required.")
        out["path"] = el["path"]
        for k in ("x", "y"):
            out[k] = _num(el.get(k), f"{w}.{k}")
        for k in ("width", "height"):
            out[k] = _num(el.get(k), f"{w}.{k}", positive=True)
    return out


def normalize_spec(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise SpecError("Spec must be a JSON object.")
    spec = copy.deepcopy(raw)

    size = spec.get("size")
    if isinstance(size, str):
        try:
            size = parse_size(size)
        except ValueError as e:
            raise SpecError(str(e))
    if not isinstance(size, dict):
        raise SpecError("size: {'width', 'height', 'unit'} required.")
    try:
        unit = normalize_unit(size.get("unit", "mm"))
    except ValueError as e:
        raise SpecError(f"size.unit: {e}")
    width = _num(size.get("width"), "size.width", positive=True)
    height = _num(size.get("height"), "size.height", positive=True)

    bleed = spec.get("bleed") or 0
    if isinstance(bleed, dict):
        try:
            b_unit = normalize_unit(bleed.get("unit", "mm"))
        except ValueError as e:
            raise SpecError(f"bleed.unit: {e}")
        b_val = _num(bleed.get("value"), "bleed.value", default=0.0)
    else:
        b_unit, b_val = "mm", _num(bleed, "bleed")
    if b_val < 0:
        raise SpecError("bleed must be >= 0.")

    scale = _num(spec.get("scale"), "scale", default=1.0, positive=True)
    if scale > 1:
        raise SpecError("scale must be <= 1 (e.g. 0.1 means a 1:10 output). Enlarging is done by choosing the real size.")

    els = spec.get("elements") or []
    if not isinstance(els, list):
        raise SpecError("elements must be a list.")
    if len(els) > MAX_ELEMENTS:
        raise SpecError(f"Too many elements ({len(els)} > {MAX_ELEMENTS}).")

    out = {
        "title": str(spec.get("title") or "Untitled design"),
        "size": {"width": width, "height": height, "unit": unit},
        "bleed": {"value": b_val, "unit": b_unit},
        "crop_marks": bool(spec.get("crop_marks", False)),
        "scale": scale,
        "background": _paint(spec.get("background"), "background"),
        "elements": [_element(e, i) for i, e in enumerate(els)],
    }
    if spec.get("notes"):
        out["notes"] = str(spec["notes"])
    return out


def page_geometry(spec: dict) -> dict:
    """Compute real-size geometry in points (before output scale) and the auto scale if needed."""
    unit = spec["size"]["unit"]
    trim_w = to_pt(spec["size"]["width"], unit)
    trim_h = to_pt(spec["size"]["height"], unit)
    bleed = to_pt(spec["bleed"]["value"], spec["bleed"]["unit"])
    slug = to_pt(8, "mm") if (spec["crop_marks"] and bleed >= 0) else 0.0
    if spec["crop_marks"]:
        slug = max(slug, to_pt(8, "mm"))
    offset = bleed + slug
    total_w = trim_w + 2 * offset
    total_h = trim_h + 2 * offset
    scale = spec["scale"]
    auto_scaled = False
    if max(total_w, total_h) * scale > MAX_PDF_SIDE_PT:
        # Choose a clean 1:N reduction so the PDF opens in every viewer.
        need = MAX_PDF_SIDE_PT / max(total_w, total_h)
        for n in (2, 4, 5, 10, 20, 25, 50, 100, 200, 500):
            if 1.0 / n <= need:
                scale = 1.0 / n
                break
        auto_scaled = True
    return {
        "trim_w": trim_w, "trim_h": trim_h, "bleed": bleed, "slug": slug, "offset": offset,
        "total_w": total_w, "total_h": total_h, "scale": scale, "auto_scaled": auto_scaled,
        "unit": unit, "u2pt": to_pt(1, unit),
    }
