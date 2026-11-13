"""Font discovery and resolution.

Drop .ttf files into the repo's `fonts/` folder (or set DESIGNER_FONT_DIRS) and they become
available by family name. System font folders are scanned too.
Only TrueType (.ttf) outlines are supported by the PDF engine.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

REPO_FONTS = Path(__file__).resolve().parent.parent / "fonts"

_SYSTEM_DIRS = [
    "/usr/share/fonts", "/usr/local/share/fonts", "~/.fonts", "~/.local/share/fonts",
    "/Library/Fonts", "~/Library/Fonts", "/System/Library/Fonts",
    "C:/Windows/Fonts",
]

_BASE14 = {
    "helvetica": ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Helvetica-BoldOblique"),
    "times": ("Times-Roman", "Times-Bold", "Times-Italic", "Times-BoldItalic"),
    "courier": ("Courier", "Courier-Bold", "Courier-Oblique", "Courier-BoldOblique"),
}
_BASE14_ALIAS = {
    "arial": "helvetica", "sans": "helvetica", "sans-serif": "helvetica", "sansserif": "helvetica",
    "timesnewroman": "times", "serif": "times", "georgia": "times",
    "mono": "courier", "monospace": "courier", "couriernew": "courier",
}

_STYLE_MAP = {
    "": "regular", "regular": "regular", "book": "regular", "roman": "regular",
    "bold": "bold",
    "italic": "italic", "oblique": "italic",
    "bolditalic": "bolditalic", "boldoblique": "bolditalic",
}


@dataclass(frozen=True)
class FontRef:
    name: str            # name known to ReportLab
    path: str | None     # TTF path (None for the built-in base-14 fonts)
    embedded: bool       # False for base-14 fonts (not embedded in PDF)


def _norm(s: str) -> str:
    return re.sub(r"[\s_\-]+", "", s.lower())


def _font_dirs() -> list[Path]:
    dirs: list[Path] = [REPO_FONTS]
    for d in os.environ.get("DESIGNER_FONT_DIRS", "").split(os.pathsep):
        if d.strip():
            dirs.append(Path(d).expanduser())
    for d in _SYSTEM_DIRS:
        p = Path(d).expanduser()
        if p.exists():
            dirs.append(p)
    return dirs


def _split_stem(stem: str) -> tuple[str, str]:
    """'Poppins-BoldItalic' -> ('Poppins', 'bolditalic'); 'DejaVuSans' -> ('DejaVuSans', 'regular')."""
    if "-" in stem:
        fam, _, style = stem.rpartition("-")
    else:
        m = re.match(r"^(.*?)(BoldItalic|BoldOblique|Bold|Italic|Oblique|Regular)$", stem)
        fam, style = (m.group(1), m.group(2)) if m and m.group(1) else (stem, "")
    st = _norm(style)
    return fam, _STYLE_MAP.get(st, st)


def _cache_file() -> Path:
    base = os.environ.get("DESIGNER_CACHE_DIR") or str(Path.home() / ".cache" / "designer-ai")
    return Path(base) / "fonts-v1.json"


def _read_names(path: str) -> tuple[str, str, str]:
    """(family, style, full name) from the font's name table; filename heuristics as a fallback."""
    stem = Path(path).stem
    try:
        from fontTools.ttLib import TTFont as _FT

        f = _FT(path, lazy=True)
        name = f["name"]
        fam = name.getDebugName(16) or name.getDebugName(1)
        sub = name.getDebugName(17) or name.getDebugName(2) or "Regular"
        full = name.getDebugName(4) or stem
        f.close()
        if fam:
            return fam, _STYLE_MAP.get(_norm(sub), _norm(sub)), full
    except Exception:
        pass
    fam, style = _split_stem(stem)
    return fam, style, stem


@lru_cache(maxsize=1)
def _index() -> tuple[dict[str, dict[str, str]], dict[str, str], dict[str, str]]:
    """Returns (families, stems, display): families[norm_family][style] = path,
    stems[norm file stem or full name] = path, display[norm_family] = readable family name."""
    cache_path = _cache_file()
    try:
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        cache = {}
    fresh: dict[str, list] = {}
    families: dict[str, dict[str, str]] = {}
    stems: dict[str, str] = {}
    display: dict[str, str] = {}
    for root in _font_dirs():
        for dirpath, _dirs, files in os.walk(root):
            for fn in sorted(files):
                if not fn.lower().endswith(".ttf") or "variable" in fn.lower():
                    continue
                path = os.path.join(dirpath, fn)
                try:
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                hit = cache.get(path)
                if hit and hit[0] == mtime:
                    fam, style, full = hit[1], hit[2], hit[3]
                else:
                    fam, style, full = _read_names(path)
                fresh[path] = [mtime, fam, style, full]
                key = _norm(fam)
                families.setdefault(key, {}).setdefault(style, path)
                display.setdefault(key, fam)
                stems.setdefault(_norm(fn[:-4]), path)
                stems.setdefault(_norm(full), path)
    if fresh != cache:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(fresh), encoding="utf-8")
        except OSError:
            pass
    return families, stems, display


_registered: set[str] = set()


def _register(path: str) -> str:
    name = "TTF_" + re.sub(r"[^A-Za-z0-9_]", "_", Path(path).stem)
    if name not in _registered:
        pdfmetrics.registerFont(TTFont(name, path))
        _registered.add(name)
    return name


def resolve_font(family: str | None, bold: bool = False, italic: bool = False) -> tuple[FontRef, str | None]:
    """Resolve a family/style request. Returns (FontRef, warning-or-None)."""
    key = _norm(family or "helvetica")
    families, stems, _ = _index()
    want = "bolditalic" if bold and italic else "bold" if bold else "italic" if italic else "regular"

    if key in families:
        styles = families[key]
        for cand in (want, "regular", next(iter(styles))):
            if cand in styles:
                p = styles[cand]
                warn = None
                if cand != want and want != "regular":
                    warn = f"Font '{family}' has no {want} style; used {cand}."
                return FontRef(_register(p), p, True), warn

    if key in stems:  # exact file stem or full font name, e.g. "Poppins-Medium"
        p = stems[key]
        return FontRef(_register(p), p, True), None

    base = _BASE14_ALIAS.get(key, key)
    if base in _BASE14:
        idx = (1 if bold else 0) + (2 if italic else 0)
        reg, b, i, bi = _BASE14[base]
        return FontRef((reg, b, i, bi)[idx], None, False), None

    reg, b, i, bi = _BASE14["helvetica"]
    idx = (1 if bold else 0) + (2 if italic else 0)
    return FontRef((reg, b, i, bi)[idx], None, False), f"Font '{family}' not found; used Helvetica."


def list_families(limit: int = 200) -> list[str]:
    _, _, display = _index()
    return ["Helvetica", "Times", "Courier"] + sorted(display.values(), key=str.lower)[:limit]


def has_arabic_script(text: str) -> bool:
    return any("\u0600" <= ch <= "\u06ff" or "\u0750" <= ch <= "\u077f" or "\ufb50" <= ch <= "\ufdff" or "\ufe70" <= ch <= "\ufeff" for ch in text)


def shape_rtl(text: str) -> str:
    """Reshape + reorder Arabic-script text (Urdu, Arabic, Persian) if the optional libs exist."""
    if not has_arabic_script(text):
        return text
    try:
        import arabic_reshaper  # type: ignore
        from bidi.algorithm import get_display  # type: ignore
    except ImportError:
        print("warning: install arabic-reshaper and python-bidi for correct Urdu/Arabic text.", file=sys.stderr)
        return text
    return get_display(arabic_reshaper.reshape(text))
