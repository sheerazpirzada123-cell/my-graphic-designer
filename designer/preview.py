"""PNG previews of the PDF (needs poppler's `pdftoppm`; optional)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def have_preview() -> bool:
    return shutil.which("pdftoppm") is not None


def pdf_to_png(pdf_path: str | Path, png_path: str | Path, max_side_px: int = 1600) -> str | None:
    exe = shutil.which("pdftoppm")
    if not exe:
        return None
    png_path = Path(png_path)
    base = png_path.with_suffix("")
    subprocess.run(
        [exe, "-png", "-singlefile", "-scale-to", str(max_side_px), str(pdf_path), str(base)],
        check=True, capture_output=True,
    )
    return str(base) + ".png"
