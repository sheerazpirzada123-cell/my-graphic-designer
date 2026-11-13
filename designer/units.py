"""Unit conversion and size parsing.

Everything is converted to PDF points (1 pt = 1/72 inch) for rendering.
"""
from __future__ import annotations

import re

MM_PER_INCH = 25.4

# points per one unit
UNIT_TO_PT: dict[str, float] = {
    "pt": 1.0,
    "px": 0.75,  # CSS pixel at 96 dpi
    "pica": 12.0,
    "in": 72.0,
    "ft": 864.0,
    "mm": 72.0 / MM_PER_INCH,
    "cm": 72.0 / (MM_PER_INCH / 10.0),
    "m": 72.0 / (MM_PER_INCH / 1000.0),
}

_ALIASES = {
    "inch": "in", "inches": "in", '"': "in", "”": "in", "''": "in",
    "foot": "ft", "feet": "ft", "'": "ft", "’": "ft",
    "millimeter": "mm", "millimeters": "mm", "millimetre": "mm", "millimetres": "mm",
    "centimeter": "cm", "centimeters": "cm", "centimetre": "cm", "centimetres": "cm",
    "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "point": "pt", "points": "pt", "pixel": "px", "pixels": "px", "picas": "pica",
}

# Paper sizes in millimetres (portrait: width, height)
PAPER_MM: dict[str, tuple[float, float]] = {
    "a0": (841, 1189), "a1": (594, 841), "a2": (420, 594), "a3": (297, 420),
    "a4": (210, 297), "a5": (148, 210), "a6": (105, 148), "a7": (74, 105),
    "b0": (1000, 1414), "b1": (707, 1000), "b2": (500, 707), "b3": (353, 500),
    "b4": (250, 353), "b5": (176, 250),
    "letter": (215.9, 279.4), "legal": (215.9, 355.6), "tabloid": (279.4, 431.8),
    "business card": (88.9, 50.8),
}

# PDF viewers (Acrobat etc.) allow at most 14400 pt (200 in) per side.
MAX_PDF_SIDE_PT = 14400.0


def normalize_unit(unit: str) -> str:
    u = (unit or "").strip().lower().rstrip(".")
    u = _ALIASES.get(u, u)
    if u not in UNIT_TO_PT:
        raise ValueError(f"Unknown unit '{unit}'. Use one of: {', '.join(sorted(UNIT_TO_PT))}")
    return u


def to_pt(value: float, unit: str) -> float:
    return float(value) * UNIT_TO_PT[normalize_unit(unit)]


def from_pt(points: float, unit: str) -> float:
    return float(points) / UNIT_TO_PT[normalize_unit(unit)]


def convert(value: float, from_unit: str, to_unit: str) -> float:
    return from_pt(to_pt(value, from_unit), to_unit)


_NUM = r"(\d+(?:\.\d+)?)"
_UNIT = r"(mm|cm|millimet\w+|centimet\w+|inch(?:es)?|in|ft|feet|foot|meters?|metres?|m|pt|px|\"|”|'|’)"
_SIZE_RE = re.compile(
    rf"{_NUM}\s*{_UNIT}?\s*(?:x|×|\*|by|X)\s*{_NUM}\s*{_UNIT}?",
    re.IGNORECASE,
)


def parse_size(text: str) -> dict:
    """Parse '3.5x2 in', '90 x 55mm', '10ft x 4ft', '3'x4'', 'A4', 'A3 landscape'.

    Returns {"width": w, "height": h, "unit": u}.
    """
    t = text.strip().lower()
    m = _SIZE_RE.search(t)
    if m:
        w, u1, h, u2 = m.group(1), m.group(2), m.group(3), m.group(4)
        unit = u2 or u1
        if not unit:
            raise ValueError(f"Size '{text}' needs a unit (mm, cm, in, ft ...).")
        if u1 and u2 and normalize_unit(u1) != normalize_unit(u2):
            # mixed units, e.g. 3ft x 24in -> convert width to height's unit
            w_val = convert(float(w), u1, u2)
            return {"width": round(w_val, 4), "height": float(h), "unit": normalize_unit(u2)}
        return {"width": float(w), "height": float(h), "unit": normalize_unit(unit)}

    for name, (pw, ph) in sorted(PAPER_MM.items(), key=lambda kv: -len(kv[0])):
        if name in t:
            if "landscape" in t:
                pw, ph = ph, pw
            return {"width": pw, "height": ph, "unit": "mm"}
    raise ValueError(f"Could not understand size '{text}'. Example: '3.5x2in', '90x55mm', '10ft x 4ft', 'A4'.")
