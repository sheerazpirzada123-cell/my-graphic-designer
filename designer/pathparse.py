"""SVG path-data parser. Produces absolute segments: ('M',x,y) ('L',x,y) ('C',x1,y1,x2,y2,x,y) ('Z',).

Quadratic curves and elliptical arcs are converted to cubic Beziers so any backend can draw them.
Coordinates stay in the caller's units (y-down).
"""
from __future__ import annotations

import math
import re

_TOKEN = re.compile(r"([MmLlHhVvCcSsQqTtAaZz])|(-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)")

_ARGC = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7, "Z": 0}


def _tokens(d: str):
    out = []
    for cmd, num in _TOKEN.findall(d):
        out.append(cmd if cmd else float(num))
    return out


def _arc_to_cubics(x1, y1, rx, ry, phi_deg, fa, fs, x2, y2):
    if rx == 0 or ry == 0 or (x1 == x2 and y1 == y2):
        return [("L", x2, y2)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg)
    cp, sp = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2.0, (y1 - y2) / 2.0
    x1p = cp * dx + sp * dy
    y1p = -sp * dx + cp * dy
    lam = (x1p ** 2) / (rx ** 2) + (y1p ** 2) / (ry ** 2)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx ** 2 * ry ** 2 - rx ** 2 * y1p ** 2 - ry ** 2 * x1p ** 2
    den = rx ** 2 * y1p ** 2 + ry ** 2 * x1p ** 2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if fa == fs:
        coef = -coef
    cxp = coef * (rx * y1p / ry)
    cyp = coef * (-ry * x1p / rx)
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2.0
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2.0

    def ang(ux, uy, vx, vy):
        a = math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)
        return a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dth > 0:
        dth -= 2 * math.pi
    elif fs and dth < 0:
        dth += 2 * math.pi

    n = max(1, math.ceil(abs(dth) / (math.pi / 2) - 1e-9))
    delta = dth / n
    t = 4.0 / 3.0 * math.tan(delta / 4.0)
    segs = []
    a = th1
    for _ in range(n):
        ca, sa = math.cos(a), math.sin(a)
        cb, sb = math.cos(a + delta), math.sin(a + delta)
        p1 = (ca - t * sa, sa + t * ca)
        p2 = (cb + t * sb, sb - t * cb)
        p3 = (cb, sb)

        def tr(p):
            px, py = p[0] * rx, p[1] * ry
            return (cp * px - sp * py + cx, sp * px + cp * py + cy)

        c1, c2, e = tr(p1), tr(p2), tr(p3)
        segs.append(("C", c1[0], c1[1], c2[0], c2[1], e[0], e[1]))
        a += delta
    # snap the last point exactly to the requested end
    last = segs[-1]
    segs[-1] = ("C", last[1], last[2], last[3], last[4], x2, y2)
    return segs


def parse_path(d: str) -> list[tuple]:
    toks = _tokens(d)
    i = 0
    segs: list[tuple] = []
    cx = cy = 0.0          # current point
    sx = sy = 0.0          # subpath start
    last_c2 = None         # last cubic control point (for S)
    last_q = None          # last quad control point (for T)
    cmd = None
    while i < len(toks):
        if isinstance(toks[i], str):
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                segs.append(("Z",))
                cx, cy = sx, sy
                last_c2 = last_q = None
                continue
        elif cmd is None:
            raise ValueError("Path data must start with a command.")
        up = cmd.upper()
        rel = cmd.islower()
        n = _ARGC[up]
        if i + n > len(toks) or any(isinstance(t, str) for t in toks[i:i + n]):
            raise ValueError(f"Bad arguments for path command '{cmd}'.")
        a = toks[i:i + n]
        i += n
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        if up == "M":
            cx, cy = a[0] + ox, a[1] + oy
            sx, sy = cx, cy
            segs.append(("M", cx, cy))
            cmd = "l" if rel else "L"  # implicit lineto after moveto
            last_c2 = last_q = None
        elif up == "L":
            cx, cy = a[0] + ox, a[1] + oy
            segs.append(("L", cx, cy))
            last_c2 = last_q = None
        elif up == "H":
            cx = a[0] + (cx if rel else 0.0)
            segs.append(("L", cx, cy))
            last_c2 = last_q = None
        elif up == "V":
            cy = a[0] + (cy if rel else 0.0)
            segs.append(("L", cx, cy))
            last_c2 = last_q = None
        elif up == "C":
            x1, y1, x2, y2, x, y = a[0] + ox, a[1] + oy, a[2] + ox, a[3] + oy, a[4] + ox, a[5] + oy
            segs.append(("C", x1, y1, x2, y2, x, y))
            last_c2 = (x2, y2)
            last_q = None
            cx, cy = x, y
        elif up == "S":
            x1, y1 = (2 * cx - last_c2[0], 2 * cy - last_c2[1]) if last_c2 else (cx, cy)
            x2, y2, x, y = a[0] + ox, a[1] + oy, a[2] + ox, a[3] + oy
            segs.append(("C", x1, y1, x2, y2, x, y))
            last_c2 = (x2, y2)
            last_q = None
            cx, cy = x, y
        elif up in ("Q", "T"):
            if up == "Q":
                qx, qy, x, y = a[0] + ox, a[1] + oy, a[2] + ox, a[3] + oy
            else:
                qx, qy = (2 * cx - last_q[0], 2 * cy - last_q[1]) if last_q else (cx, cy)
                x, y = a[0] + ox, a[1] + oy
            segs.append((
                "C",
                cx + 2.0 / 3.0 * (qx - cx), cy + 2.0 / 3.0 * (qy - cy),
                x + 2.0 / 3.0 * (qx - x), y + 2.0 / 3.0 * (qy - y),
                x, y,
            ))
            last_q = (qx, qy)
            last_c2 = None
            cx, cy = x, y
        elif up == "A":
            rx, ry, rot, fa, fs, x, y = a[0], a[1], a[2], int(a[3]), int(a[4]), a[5] + ox, a[6] + oy
            segs.extend(_arc_to_cubics(cx, cy, rx, ry, rot, fa, fs, x, y))
            cx, cy = x, y
            last_c2 = last_q = None
    return segs
