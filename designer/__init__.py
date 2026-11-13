"""designer-ai: voice / text / image instructions -> print-ready CMYK vector design files."""
from .render_pdf import RenderReport, render_pdf
from .render_svg import render_svg
from .spec import SpecError, normalize_spec

__all__ = ["render_pdf", "render_svg", "normalize_spec", "SpecError", "RenderReport"]
__version__ = "0.1.0"
