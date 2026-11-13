"""Colour handling. CMYK is the native colour model; RGB is only approximated for previews/SVG."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class CMYK:
    c: float  # 0-100
    m: float
    y: float
    k: float

    def unit(self) -> tuple[float, float, float, float]:
        return (self.c / 100.0, self.m / 100.0, self.y / 100.0, self.k / 100.0)

    def to_rgb(self) -> tuple[int, int, int]:
        """Approximate screen colour (SWOP-like), used for SVG and previews only.

        Multilinear interpolation between the sixteen CMYK corner colours; far closer to what a
        print looks like on screen than the naive (1-c)(1-k) formula, which is oversaturated.
        """
        c, m, y, k = self.unit()
        out = [0.0, 0.0, 0.0]
        for (ci, mi, yi), base in _CORNERS.items():
            w = (c if ci else 1 - c) * (m if mi else 1 - m) * (y if yi else 1 - y)
            if w == 0:
                continue
            for ch in range(3):
                black = _K_RGB[ch] * base[ch] / 255.0
                out[ch] += w * ((1 - k) * base[ch] + k * black)
        return tuple(max(0, min(255, round(v))) for v in out)  # type: ignore[return-value]

    def to_hex(self) -> str:
        return "#%02x%02x%02x" % self.to_rgb()


# RGB of the eight C/M/Y corners at K=0 (typical coated-paper SWOP appearance) and of 100% K.
_CORNERS = {
    (0, 0, 0): (255, 255, 255), (1, 0, 0): (0, 174, 239), (0, 1, 0): (236, 0, 140), (0, 0, 1): (255, 242, 0),
    (1, 1, 0): (46, 49, 146), (1, 0, 1): (0, 166, 81), (0, 1, 1): (237, 28, 36), (1, 1, 1): (54, 54, 56),
}
_K_RGB = (35, 31, 32)


def _clamp(v: float) -> float:
    return max(0.0, min(100.0, float(v)))


def rgb_to_cmyk(r: float, g: float, b: float) -> CMYK:
    """Naive RGB -> CMYK (no ICC profile). Use real CMYK values for print-critical colours."""
    r, g, b = r / 255.0, g / 255.0, b / 255.0
    k = 1 - max(r, g, b)
    if k >= 1:
        return CMYK(0, 0, 0, 100)
    c = (1 - r - k) / (1 - k)
    m = (1 - g - k) / (1 - k)
    y = (1 - b - k) / (1 - k)
    return CMYK(_clamp(c * 100), _clamp(m * 100), _clamp(y * 100), _clamp(k * 100))


# Common names mapped to deliberate CMYK builds (not naive conversion).
NAMED: dict[str, CMYK] = {
    "black": CMYK(0, 0, 0, 100),
    "white": CMYK(0, 0, 0, 0),
    "red": CMYK(0, 100, 100, 0),
    "green": CMYK(100, 0, 100, 0),
    "blue": CMYK(100, 100, 0, 0),
    "cyan": CMYK(100, 0, 0, 0),
    "magenta": CMYK(0, 100, 0, 0),
    "yellow": CMYK(0, 0, 100, 0),
    "orange": CMYK(0, 50, 100, 0),
    "purple": CMYK(60, 90, 0, 0),
    "pink": CMYK(0, 50, 0, 0),
    "brown": CMYK(0, 75, 100, 50),
    "gray": CMYK(0, 0, 0, 50),
    "grey": CMYK(0, 0, 0, 50),
    "lightgray": CMYK(0, 0, 0, 20),
    "lightgrey": CMYK(0, 0, 0, 20),
    "darkgray": CMYK(0, 0, 0, 75),
    "darkgrey": CMYK(0, 0, 0, 75),
    "navy": CMYK(100, 90, 30, 30),
    "gold": CMYK(10, 30, 100, 10),
    "maroon": CMYK(20, 100, 90, 40),
    "teal": CMYK(100, 0, 40, 40),
    "rich black": CMYK(60, 40, 40, 100),
    "registration": CMYK(100, 100, 100, 100),
}

_CMYK_FN = re.compile(r"^cmyk\(\s*([\d.]+)%?\s*[, ]\s*([\d.]+)%?\s*[, ]\s*([\d.]+)%?\s*[, ]\s*([\d.]+)%?\s*\)$", re.I)
_RGB_FN = re.compile(r"^rgb\(\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*[, ]\s*([\d.]+)\s*\)$", re.I)
_HEX = re.compile(r"^#?([0-9a-f]{3}|[0-9a-f]{6})$", re.I)


def parse_color(value) -> CMYK | None:
    """Return CMYK, or None for 'no paint'.

    Accepts: {"c":..,"m":..,"y":..,"k":..} (0-100), "cmyk(0,100,100,0)", "#RRGGBB",
    "rgb(r,g,b)", a colour name, or None / "none" / "transparent".
    """
    if value is None:
        return None
    if isinstance(value, CMYK):
        return value
    if isinstance(value, dict):
        if {"c", "m", "y", "k"} <= set(value):
            return CMYK(_clamp(value["c"]), _clamp(value["m"]), _clamp(value["y"]), _clamp(value["k"]))
        if {"r", "g", "b"} <= set(value):
            return rgb_to_cmyk(value["r"], value["g"], value["b"])
        raise ValueError(f"Bad colour object: {value}")
    if isinstance(value, (list, tuple)) and len(value) == 4:
        return CMYK(*[_clamp(v) for v in value])
    if not isinstance(value, str):
        raise ValueError(f"Bad colour: {value!r}")
    s = value.strip().lower()
    if s in ("", "none", "transparent"):
        return None
    if s in NAMED:
        return NAMED[s]
    m = _CMYK_FN.match(s)
    if m:
        return CMYK(*[_clamp(float(g)) for g in m.groups()])
    m = _RGB_FN.match(s)
    if m:
        return rgb_to_cmyk(*[float(g) for g in m.groups()])
    m = _HEX.match(s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        return rgb_to_cmyk(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
    raise ValueError(f"Unknown colour '{value}'. Use CMYK values like {{\"c\":100,\"m\":80,\"y\":0,\"k\":10}}.")
