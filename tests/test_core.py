import json
import math
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from helpers import colour_ops, media_box, pdf_content  # noqa: E402

from designer import SpecError, normalize_spec, render_pdf, render_svg  # noqa: E402
from designer.colors import parse_color  # noqa: E402
from designer.pathparse import parse_path  # noqa: E402
from designer.units import convert, parse_size, to_pt  # noqa: E402

EXAMPLES = Path(__file__).parent.parent / "examples"


# ---------------------------------------------------------------- units
def test_unit_conversions():
    assert to_pt(1, "in") == 72
    assert to_pt(25.4, "mm") == pytest.approx(72)
    assert to_pt(2.54, "cm") == pytest.approx(72)
    assert to_pt(1, "ft") == 864
    assert convert(10, "ft", "in") == pytest.approx(120)


@pytest.mark.parametrize("text,expect", [
    ("3.5x2 in", (3.5, 2, "in")),
    ("90 x 55mm", (90, 55, "mm")),
    ("10ft x 4ft", (10, 4, "ft")),
    ("12\" x 18\"", (12, 18, "in")),
    ("3' x 4'", (3, 4, "ft")),
    ("A4", (210, 297, "mm")),
    ("a3 landscape", (420, 297, "mm")),
    ("2ft x 24in", (24, 24, "in")),
])
def test_parse_size(text, expect):
    s = parse_size(text)
    assert (s["width"], s["height"], s["unit"]) == expect


def test_parse_size_needs_unit():
    with pytest.raises(ValueError):
        parse_size("3 x 4")


# ---------------------------------------------------------------- colours
def test_colour_forms():
    assert parse_color({"c": 10, "m": 20, "y": 30, "k": 40}).k == 40
    assert parse_color("cmyk(0,100,100,0)").m == 100
    assert parse_color("black").unit() == (0, 0, 0, 1)  # pure K black, no CMY
    assert parse_color("#ffffff").unit() == (0, 0, 0, 0)
    assert parse_color("none") is None
    with pytest.raises(ValueError):
        parse_color("not-a-colour")


def test_cmyk_to_rgb_preview_is_sane():
    from designer.colors import CMYK

    assert CMYK(0, 0, 0, 0).to_rgb() == (255, 255, 255)
    assert max(CMYK(0, 0, 0, 100).to_rgb()) < 40
    r, g, b = CMYK(100, 85, 20, 25).to_rgb()   # navy: dark blue, not electric blue
    assert b > r and b > g and r < 70 and b < 160
    assert CMYK(0, 100, 100, 0).to_rgb()[0] > 200  # red stays red


# ---------------------------------------------------------------- paths
def test_arc_circle_bounds():
    segs = parse_path("M10 50 a40 40 0 1 0 80 0 a40 40 0 1 0 -80 0 Z")
    xs, ys = [], []
    for s in segs:
        v = s[1:]
        xs += v[0::2]
        ys += v[1::2]
    # control points stay near the circle's bounding box (10..90, 10..90)
    assert min(xs) >= 9 and max(xs) <= 91
    assert min(ys) >= 9 and max(ys) <= 91
    assert segs[-1] == ("Z",)


def test_relative_commands():
    segs = parse_path("m10 10 l5 0 v5 h-5 z")
    assert segs[:4] == [("M", 10, 10), ("L", 15, 10), ("L", 15, 15), ("L", 10, 15)]


def test_bad_path_rejected():
    with pytest.raises(SpecError):
        normalize_spec({"size": {"width": 1, "height": 1, "unit": "in"},
                        "elements": [{"type": "path", "d": "M 10", "fill": "black"}]})


# ---------------------------------------------------------------- spec
def _base(**kw):
    s = {"size": {"width": 3.5, "height": 2, "unit": "in"}, "elements": []}
    s.update(kw)
    return s


def test_normalize_is_idempotent_and_cmyk_only():
    spec = normalize_spec(_base(background="#ff0000", elements=[
        {"type": "rect", "x": 0, "y": 0, "width": 1, "height": 1, "fill": "#00ff00"},
        {"type": "text", "text": "Hi", "x": 1, "y": 1, "size": 0.5, "size_unit": "in"},
    ]))
    assert normalize_spec(spec) == spec
    assert set(spec["background"]) == {"c", "m", "y", "k"}
    assert spec["elements"][1]["size"] == 36  # 0.5 in -> pt


@pytest.mark.parametrize("bad", [
    _base(scale=2),
    {"size": {"width": 0, "height": 2, "unit": "in"}},
    {"size": {"width": 1, "height": 1, "unit": "parsec"}},
    _base(elements=[{"type": "blob"}]),
    _base(elements=[{"type": "rect", "x": 0, "y": 0, "width": 1}]),
    _base(elements=[{"type": "line", "x1": 0, "y1": 0, "x2": 1, "y2": 1}]),
])
def test_invalid_specs(bad):
    with pytest.raises(SpecError):
        normalize_spec(bad)


# ---------------------------------------------------------------- rendering
def test_exact_page_size_and_cmyk(tmp_path):
    spec = _base(elements=[
        {"type": "rect", "x": 0.1, "y": 0.1, "width": 1, "height": 1, "fill": {"c": 100, "m": 0, "y": 0, "k": 0},
         "stroke": {"c": 0, "m": 0, "y": 0, "k": 100}},
        {"type": "text", "text": "Hello", "x": 0.2, "y": 1.5, "size": 12, "font": "Helvetica"},
    ])
    out = tmp_path / "a.pdf"
    r = render_pdf(spec, out)
    assert media_box(out) == (0, 0, 252.0, 144.0)  # 3.5 x 2 in exactly
    assert colour_ops(out) <= {"k", "K"}
    assert r.scale == 1


def test_millimetre_size(tmp_path):
    out = tmp_path / "a.pdf"
    render_pdf({"size": {"width": 90, "height": 55, "unit": "mm"}, "elements": []}, out)
    w, h = media_box(out)[2:]
    assert w == pytest.approx(90 * 72 / 25.4, abs=0.01)
    assert h == pytest.approx(55 * 72 / 25.4, abs=0.01)


def test_bleed_and_crop_marks_grow_the_page(tmp_path):
    out = tmp_path / "a.pdf"
    render_pdf(_base(bleed={"value": 0.125, "unit": "in"}, crop_marks=True), out)
    data = open(out, "rb").read()
    w, h = media_box(out)[2:]
    assert w > 252 + 2 * 9 and h > 144 + 2 * 9
    assert b"/TrimBox" in data and b"/BleedBox" in data


def test_huge_format_is_auto_scaled(tmp_path):
    out = tmp_path / "a.pdf"
    r = render_pdf({"size": {"width": 300, "height": 20, "unit": "ft"}, "elements": []}, out)
    assert r.auto_scaled and r.scale < 1
    assert max(media_box(out)[2:]) <= 14400
    assert any("scale" in w.lower() for w in r.warnings)


def test_gradient_stays_cmyk(tmp_path):
    out = tmp_path / "a.pdf"
    grad = {"type": "radial", "cx": 1, "cy": 1, "r": 1, "stops": [
        {"offset": 0, "color": {"c": 0, "m": 100, "y": 100, "k": 0}},
        {"offset": 1, "color": {"c": 100, "m": 0, "y": 0, "k": 0}}]}
    render_pdf(_base(elements=[{"type": "rect", "x": 0, "y": 0, "width": 3.5, "height": 2, "fill": grad}]), out)
    data = open(out, "rb").read()
    assert b"DeviceRGB" not in data
    assert b"DeviceCMYK" in data


def test_text_is_outlined_when_font_is_ttf(tmp_path):
    from designer.fonts import resolve_font

    ref, _ = resolve_font("Poppins", bold=True)
    if not ref.path:
        pytest.skip("Poppins not installed")
    out = tmp_path / "a.pdf"
    r = render_pdf(_base(elements=[{"type": "text", "text": "Outlined", "x": 0.2, "y": 1, "size": 20, "font": "Poppins", "bold": True}]), out)
    assert r.live_fonts == []
    assert b"BT" not in pdf_content(out).replace(b"BT /F1 12 Tf", b"")


def test_rotation_and_opacity_render(tmp_path):
    out = tmp_path / "a.pdf"
    render_pdf(_base(elements=[
        {"type": "rect", "x": 1, "y": 0.5, "width": 1, "height": 0.5, "fill": "red", "rotate": 30, "opacity": 0.5}]), out)
    assert out.stat().st_size > 500


def test_image_outside_assets_is_blocked(tmp_path):
    secret = tmp_path / "secret.png"
    from PIL import Image
    Image.new("RGB", (4, 4), "red").save(secret)
    assets = tmp_path / "assets"
    assets.mkdir()
    r = render_pdf(_base(elements=[{"type": "image", "path": "../secret.png", "x": 0, "y": 0, "width": 1, "height": 1}]),
                   tmp_path / "a.pdf", assets_dir=assets)
    assert any("outside" in w for w in r.warnings)


def test_svg_is_valid_xml_with_physical_size(tmp_path):
    spec = json.loads((EXAMPLES / "business_card.json").read_text())
    out = tmp_path / "a.svg"
    render_svg(spec, out)
    root = ET.parse(out).getroot()
    assert root.tag.endswith("svg")
    assert root.attrib["width"].endswith("in")


@pytest.mark.parametrize("name", ["business_card", "banner_feet"])
def test_examples_render(name, tmp_path):
    spec = json.loads((EXAMPLES / f"{name}.json").read_text())
    r = render_pdf(spec, tmp_path / "a.pdf")
    render_svg(spec, tmp_path / "a.svg")
    assert colour_ops(tmp_path / "a.pdf") <= {"k", "K"}
    assert math.isfinite(r.page_width_pt)
