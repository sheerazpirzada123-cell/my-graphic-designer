# designer-ai

Tell it what you want - **by voice (any language), by text, or with a reference image** - and get back a
**print-ready vector design in CMYK**, at the **exact size** you ask for (inches, feet, mm, cm, m, pt, px).

```
voice / text / image ──► Claude designs a spec ──► CMYK vector PDF  (+ SVG, PNG preview, editable .json)
```

> **Roman Urdu mein khulasa:** voice note (Urdu ya koi bhi zubaan), text, ya image bhejein aur batayein ke kya
> design chahiye aur kis size mein (inch, feet, mm, cm). Tool CMYK vector PDF bana kar deta hai. Phir
> "logo bara karo, color red kar do" jaisi changes bolein - woh pichle design par apply hoti hain (version 1, 2, 3 ...).

## What you get

| Output | Details |
|---|---|
| `design_vNNN.pdf` | **Vector, DeviceCMYK only** (no RGB). Exact page size, TrimBox/BleedBox, optional crop marks. Text is converted to outlines for TTF fonts. Open in Illustrator, CorelDRAW, Affinity, Acrobat. |
| `design_vNNN.svg` | Vector for Figma/Inkscape/Corel. SVG has no CMYK, so colours here are RGB approximations. Use the PDF for print. |
| `design_vNNN.png` | Preview (needs `pdftoppm` from poppler). |
| `design_vNNN.json` | The editable design spec. Every colour is explicit CMYK. Re-render any time, no AI needed. |

Features: exact units, bleed + crop marks, gradients, curves/arcs (SVG path syntax), rotation, opacity, dashed
lines, text wrapping, letter-spacing, right-to-left scripts (Urdu/Arabic), 1:N scale for giant formats
(a PDF page cannot exceed 200 in, so a 300 ft banner is exported at e.g. 1:10 automatically and you are told).

## Install

```bash
git clone <your-repo-url> designer-ai && cd designer-ai
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt                          # or: pip install -e ".[all]"
cp .env.example .env                                     # then put your ANTHROPIC_API_KEY in .env
```

Optional: install **poppler** for PNG previews and the AI visual self-check
(`sudo apt install poppler-utils`, `brew install poppler`, or poppler for Windows).

## Use

```bash
# 1. Text (any language)
designer make "Visiting card 3.5 x 2 inch, navy blue and orange, name Ali Khan, phone 0300 1234567" -p ali-card

# 2. Voice note (Urdu, Hindi, Punjabi, Arabic, English ... auto-detected)
designer make --audio note.m4a -p ali-card
designer make --audio note.m4a --whisper-model medium --language ur    # more accurate for Urdu

# 3. Reference image + your changes
designer make --image reference.jpg "same design, change the colour to green, name Ahmed, size 90x55mm" -p card2

# 4. Revise (same project name = builds on the latest version)
designer make -p ali-card "logo bigger, make the orange line thicker"
designer make -p ali-card --new "start again, something more minimal"

# Force an exact size, whatever the instruction says
designer make "flex banner for a shoe shop" --size "10ft x 4ft" -p flex

# Re-render a spec without AI / list usable fonts
designer render designs/ali-card/design_v002.json --size "A5"
designer fonts
```

Sizes accepted: `3.5x2in`, `90 x 55 mm`, `10ft x 4ft`, `12" x 18"`, `3' x 4'`, `A0-A7`, `B0-B5`, `Letter`, `Legal`, `A3 landscape`.
Results go to `designs/<project>/` (change with `DESIGNER_HOME`).

### Web app (record voice from your phone or laptop)

```bash
designer serve                 # http://127.0.0.1:8000
```

Type, press **Record voice**, attach reference images, choose a size, and download the PDF/SVG.
It listens on localhost only. To expose it, set `DESIGNER_TOKEN` (a password) first - otherwise anyone who
finds the address could spend your API credits. The server refuses to start on a public address without it.

## Voice in Urdu / other languages

* Transcription runs **locally** with faster-whisper (free, private). First use downloads the model.
  `small` is the default; use `medium` or `large-v3` for better Urdu accuracy (`--whisper-model`, or `WHISPER_MODEL` in `.env`).
* Prefer the OpenAI transcription API? Set `STT_ENGINE=openai` and `OPENAI_API_KEY`.
* The transcript is printed so you can see what it heard. Claude copes with small recognition mistakes.
* **Urdu text on the design:** `pip install arabic-reshaper python-bidi`, and put an Urdu TrueType font
  (e.g. *Noto Naskh Arabic*, *Jameel Noori Nastaleeq*) into `fonts/`. Arabic-script text is kept as live
  embedded text (not outlines) so letters join correctly.

## Fonts

Any `.ttf` in `fonts/`, in `DESIGNER_FONT_DIRS`, or installed on the system is found by family name
(`designer fonts`). Claude is told exactly which fonts exist and only uses those. Helvetica/Times/Courier
are built in but are not embedded in the PDF.

## How it works

```
designer/
  units.py        mm/cm/in/ft/m/pt/px conversion, size and paper-name parsing
  colors.py       CMYK model, name/hex/rgb -> CMYK
  spec.py         validation + normalisation of the JSON design spec; bleed/slug/scale geometry
  pathparse.py    SVG path data (incl. arcs, quadratics) -> cubic Beziers
  fonts.py        font discovery (reads real family/style names, cached)
  render_pdf.py   CMYK vector PDF (ReportLab) with text-to-outlines (fontTools)
  render_svg.py   SVG export
  schema.py       the create_design tool schema + the designer system prompt
  llm.py          Claude call (vision in, spec out, validate-and-retry)
  transcribe.py   voice -> text
  pipeline.py     orchestration, visual self-check, versioned projects
  cli.py, server.py, static/index.html
examples/         ready-made specs (business card, A4 flyer, 10x4 ft banner)
tests/            pytest suite
```

The spec is plain JSON (origin top-left, y down, all geometry in the page's unit, font size and stroke in points):

```json
{
  "title": "Card",
  "size": {"width": 3.5, "height": 2, "unit": "in"},
  "bleed": {"value": 0.125, "unit": "in"},
  "crop_marks": true,
  "background": {"c": 0, "m": 0, "y": 0, "k": 0},
  "elements": [
    {"type": "rect", "x": 0, "y": 0, "width": 1.2, "height": 2, "fill": {"c": 100, "m": 85, "y": 20, "k": 25}},
    {"type": "text", "text": "ALI KHAN", "x": 1.45, "y": 0.62, "size": 17, "font": "Poppins", "bold": true,
     "fill": {"c": 0, "m": 0, "y": 0, "k": 100}}
  ]
}
```

Element types: `rect`, `ellipse`, `line`, `polygon`, `polyline`, `path`, `text`, `image`. See `examples/` and `designer/schema.py`.

After the first draft the model is shown a **render of its own design** (and your reference image) and fixes
overlaps, clipped text and proportion errors (`--refine 0` turns this off, `--refine 2` does two passes).

## Honest limitations

* **Image -> vector is a reconstruction, not auto-tracing.** Claude looks at your image and rebuilds the
  layout, colours, text and simple shapes as vector. Logos, illustrations and photos with complex detail will be
  approximated. For pixel-exact logo tracing use a tracer (Illustrator Image Trace, Inkscape, vectorizer tools)
  and combine the result with the generated layout.
* **CMYK is device CMYK** (no ICC profile / PDF/X conversion). Colours seen in a reference image are converted
  to a sensible CMYK build, so always proof before a large print run. A prepress step (e.g. Acrobat Preflight)
  can add PDF/X-1a or an output intent.
* Output is PDF and SVG. There is no native `.ai`, `.cdr` or `.eps` writer; open the PDF in Illustrator/CorelDRAW and save as needed.
* Fonts must be TrueType (`.ttf`). OpenType-CFF `.otf` fonts are ignored.
* Raster images placed with an `image` element stay raster (RGB).
* Each design costs Anthropic API usage (one call, plus one per self-check pass).

## Develop

```bash
pip install -e ".[dev]" && pytest -q
```

## License

MIT
