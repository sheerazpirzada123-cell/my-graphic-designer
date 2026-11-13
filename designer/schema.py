"""JSON schema for the `create_design` tool + the designer system prompt."""
from __future__ import annotations

from .fonts import list_families

_COLOR = (
    "Colour in CMYK, each channel 0-100: {\"c\":100,\"m\":80,\"y\":0,\"k\":10}. "
    "Or a gradient: {\"type\":\"linear\",\"x1\":..,\"y1\":..,\"x2\":..,\"y2\":..,\"stops\":[{\"offset\":0,\"color\":{cmyk}},{\"offset\":1,\"color\":{cmyk}}]} "
    "or {\"type\":\"radial\",\"cx\":..,\"cy\":..,\"r\":..,\"stops\":[...]} (gradient coordinates use the page unit). "
    "Use null for no paint."
)

SPEC_JSON_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Short name of the design."},
        "size": {
            "type": "object",
            "description": "Final trimmed size in real-world units. Use exactly the size the user asked for.",
            "properties": {
                "width": {"type": "number"},
                "height": {"type": "number"},
                "unit": {"type": "string", "enum": ["mm", "cm", "in", "ft", "m", "pt", "px"]},
            },
            "required": ["width", "height", "unit"],
        },
        "bleed": {
            "type": "object",
            "description": "Print bleed on every side (3 mm / 0.125 in is standard). Omit for screen use.",
            "properties": {"value": {"type": "number"}, "unit": {"type": "string", "enum": ["mm", "cm", "in", "pt"]}},
            "required": ["value", "unit"],
        },
        "crop_marks": {"type": "boolean", "description": "Add crop/trim marks (only useful with bleed)."},
        "scale": {
            "type": "number",
            "description": "Optional output scale <= 1 (0.1 = 1:10) for very large formats. Usually omit.",
        },
        "background": {"description": _COLOR},
        "elements": {
            "type": "array",
            "description": "Drawn back-to-front. Coordinates use the size.unit; origin is top-left, y grows downward.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["rect", "ellipse", "line", "polygon", "polyline", "path", "text"]},
                    "name": {"type": "string", "description": "Human label, e.g. 'logo circle'."},
                    "x": {"type": "number"}, "y": {"type": "number"},
                    "width": {"type": "number"}, "height": {"type": "number"},
                    "radius": {"type": "number", "description": "rect corner radius (page unit)."},
                    "cx": {"type": "number"}, "cy": {"type": "number"},
                    "rx": {"type": "number"}, "ry": {"type": "number"},
                    "r": {"type": "number", "description": "ellipse: circle radius shortcut."},
                    "x1": {"type": "number"}, "y1": {"type": "number"}, "x2": {"type": "number"}, "y2": {"type": "number"},
                    "points": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
                    "d": {"type": "string", "description": "path: SVG path data (M L H V C S Q T A Z, absolute or relative) in page-unit coordinates."},
                    "fill_rule": {"type": "string", "enum": ["nonzero", "evenodd"]},
                    "fill": {"description": _COLOR},
                    "stroke": {"description": _COLOR},
                    "stroke_width": {"type": "number", "description": "Points (1 pt = 1/72 in)."},
                    "cap": {"type": "string", "enum": ["butt", "round", "square"]},
                    "join": {"type": "string", "enum": ["miter", "round", "bevel"]},
                    "dash": {"type": "array", "items": {"type": "number"}, "description": "Dash pattern in points."},
                    "opacity": {"type": "number", "description": "0-1."},
                    "rotate": {"type": "number", "description": "Degrees clockwise about the element centre (text: about x,y)."},
                    "text": {"type": "string", "description": "Use \\n for line breaks."},
                    "size": {"type": "number", "description": "text: font size in points unless size_unit is given."},
                    "size_unit": {"type": "string", "enum": ["pt", "mm", "cm", "in", "ft", "m"], "description": "text: unit for size. Handy for banners (e.g. size 8, size_unit 'in')."},
                    "font": {"type": "string", "description": "Font family from the available list."},
                    "bold": {"type": "boolean"}, "italic": {"type": "boolean"},
                    "align": {"type": "string", "enum": ["left", "center", "right"], "description": "Alignment relative to x. y is the first baseline."},
                    "line_height": {"type": "number", "description": "Multiplier, default 1.2."},
                    "letter_spacing": {"type": "number", "description": "Points."},
                    "max_width": {"type": "number", "description": "Wrap text at this width (page unit)."},
                    "rtl": {"type": "boolean", "description": "Right-to-left script (Urdu/Arabic/Persian)."},
                    "path": {"type": "string", "description": "image: file name from the supplied assets."},
                },
                "required": ["type"],
            },
        },
        "notes": {
            "type": "string",
            "description": "Short plain-language notes for the user: assumptions made (sizes, fonts, colours) in the user's own language.",
        },
    },
    "required": ["title", "size", "elements"],
}

TOOL = {
    "name": "create_design",
    "description": "Create or fully replace the vector design. Always send the complete spec.",
    "input_schema": SPEC_JSON_SCHEMA,
}


def system_prompt() -> str:
    fonts = ", ".join(list_families(120))
    return f"""You are a senior print and graphic designer working in a professional vector workflow (like Illustrator or CorelDRAW). \
You turn the user's instructions - spoken (already transcribed), typed, or given with a reference image - into an exact, print-ready vector design \
by calling the create_design tool. Always answer by calling the tool; never reply with prose only.

LANGUAGE
- Instructions can be in any language or a mix (Urdu, Roman Urdu, Hindi, Arabic, Punjabi, English ...). Voice transcripts may contain recognition mistakes; use common sense and keep names, phone numbers and prices exactly as given.
- The text that appears ON the design must be in the language the user wants. If unclear, use the language of the instruction. Write the 'notes' field in the user's language.
- Urdu/Arabic/Persian text: set "rtl": true, use align "right" or "center", and pick a font from the list that supports the script if one exists.

SIZE AND UNITS
- Use exactly the size the user states (any of mm, cm, in, ft, m, pt, px). Never convert it yourself; put the number and unit in size. If a standard format is named (A4, business card, visiting card, flex, standee, letterhead...), use its standard size. If no size is given, choose the most standard size for that product and say so in notes.
- ALL geometry (x, y, width, height, radius, cx, cy, points, path d, gradient coordinates) uses size.unit. Origin is the top-left corner, y grows downward. Do the arithmetic carefully so elements sit exactly where intended.
- Font sizes and stroke widths are in points. For very large formats (banners, flex, hoardings) use size_unit on text (e.g. size 10, size_unit "in") so type is proportional to the page.

COLOUR (CMYK ONLY)
- Every colour is CMYK 0-100. Never use RGB/hex. Convert colours seen in a reference image to the closest sensible CMYK build.
- Body text and thin lines: pure black is {{"c":0,"m":0,"y":0,"k":100}}. Avoid total ink over ~300% on large areas.
- Keep contrast high enough to read.

PRINT PRACTICE
- For anything that will be printed and cut (cards, flyers, posters, stickers, banners, brochures) add bleed 3 mm (0.125 in; banners 1 in) and crop_marks true unless the user says otherwise or wants screen use. Backgrounds and full-bleed shapes must extend past the trim edge by the bleed (use background plus shapes that start at negative coordinates and run past width/height).
- Keep text and key elements at least 3-5 mm (0.15-0.2 in) inside the trim edge.
- Build logos, icons and illustrations from shapes and paths (vector). Do not use raster images unless the user explicitly asks to place a supplied photo as-is (then use an image element with the supplied file name).
- Do not recreate real third-party brand logos or trademarks unless the user says the brand is theirs.

FONTS
- Available font families: {fonts}. Use only these names. Helvetica/Times/Courier are built in but not embedded; prefer an installed family for professional output.

REFERENCE IMAGES
- When the user gives an image and asks for "the same" design, rebuild it faithfully as vector: same layout, proportions, colours, text content and alignment, and as many of the original shapes as you can. Estimate positions from the image proportions and scale them to the target size. Then apply every change the user lists. Anything the user did not mention stays the same.
- If the image is only inspiration, follow the user's words and use the image for style.

REVISIONS
- If a current design spec is provided, change only what the user asked and keep everything else identical (same size unless told to change it). Return the full updated spec.

QUALITY
- Think like a designer: clear hierarchy, consistent margins and alignment, balanced spacing, sensible type pairing, no overlapping text, nothing outside the page except intentional bleed.
- Text width: estimate it (average glyph width is about 0.5 x font size) so lines fit; use max_width and \\n when needed."""
