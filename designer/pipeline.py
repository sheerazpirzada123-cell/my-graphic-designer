"""End-to-end flow: voice/text/image -> spec -> (self-check) -> CMYK PDF + SVG + PNG."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import llm, project, transcribe
from .preview import have_preview, pdf_to_png
from .render_pdf import RenderReport, render_pdf
from .render_svg import render_svg
from .spec import normalize_spec
from .units import parse_size


@dataclass
class Result:
    project: str
    version: int
    spec: dict
    files: dict[str, str]
    report: RenderReport
    notes: str = ""
    transcripts: list[str] = field(default_factory=list)
    instruction: str = ""


def export(spec: dict, stem: Path, formats: set[str], assets_dir: Path | None = None) -> tuple[dict[str, str], RenderReport]:
    """Write design files next to `stem` (no extension). PDF is always produced."""
    files: dict[str, str] = {}
    pdf = stem.with_suffix(".pdf")
    report = render_pdf(spec, pdf, assets_dir=assets_dir)
    files["pdf"] = str(pdf)
    if "svg" in formats:
        files["svg"] = render_svg(spec, stem.with_suffix(".svg"))
    if "png" in formats and have_preview():
        png = pdf_to_png(pdf, stem.with_suffix(".png"))
        if png:
            files["png"] = png
    return files, report


def run(
    instruction: str = "",
    audio: list[str | Path] | None = None,
    images: list[str | Path] | None = None,
    project_name: str | None = None,
    new: bool = False,
    size: str | None = None,
    formats: set[str] | None = None,
    refine: int = 1,
    model: str | None = None,
    language: str | None = None,
    whisper_model: str | None = None,
    on_status=lambda msg: None,
) -> Result:
    formats = formats or {"pdf", "svg", "png"}
    images = [Path(p) for p in (images or [])]

    transcripts: list[str] = []
    for a in audio or []:
        on_status(f"Transcribing {Path(a).name} ...")
        text, lang = transcribe.transcribe(a, language=language, model_size=whisper_model)
        transcripts.append(text)
        on_status(f"Heard ({lang or 'auto'}): {text}")
    full_instruction = "\n".join(t for t in [instruction.strip(), *transcripts] if t)
    if not full_instruction and not images:
        raise ValueError("Give at least one of: text, a voice recording, or an image.")

    name = project_name or project.new_project_name()
    pdir = project.project_dir(name)

    asset_names: list[str] = []
    for p in images:
        dest = pdir / "assets" / project.safe_asset_name(p.name)
        if not dest.exists() or dest.resolve() != p.resolve():
            shutil.copyfile(p, dest)
        asset_names.append(dest.name)

    base_spec, base_version = None, 0
    if not new:
        latest = project.load_latest(pdir)
        if latest:
            base_version, base_spec = latest

    size_override = parse_size(size) if size else None

    on_status("Designing ...")
    spec = llm.generate_spec(
        full_instruction, images=images, base_spec=base_spec, size_hint=size_override,
        asset_names=asset_names, model=model,
    )
    if size_override:
        spec["size"] = size_override
        spec = normalize_spec(spec)

    if refine > 0 and have_preview():
        tmp_pdf = pdir / ".check.pdf"
        for i in range(refine):
            on_status(f"Self-check {i + 1}/{refine} ...")
            render_pdf(spec, tmp_pdf, assets_dir=pdir / "assets")
            png = pdf_to_png(tmp_pdf, pdir / ".check.png", 1400)
            if not png:
                break
            fixed = llm.refine_spec(spec, png, full_instruction, images=images, model=model)
            if size_override:
                fixed["size"] = size_override
                fixed = normalize_spec(fixed)
            if fixed == spec:
                break
            spec = fixed
        for tmp in (tmp_pdf, pdir / ".check.png"):
            tmp.unlink(missing_ok=True)

    version = (project.versions(pdir) or [0])[-1] + 1
    stem = project.stem(pdir, version)
    stem.with_suffix(".json").write_text(json.dumps(spec, indent=2, ensure_ascii=False), encoding="utf-8")
    files, report = export(spec, stem, formats, assets_dir=pdir / "assets")
    files["json"] = str(stem.with_suffix(".json"))
    project.log_history(pdir, version, full_instruction)

    return Result(
        project=pdir.name, version=version, spec=spec, files=files, report=report,
        notes=spec.get("notes", ""), transcripts=transcripts, instruction=full_instruction,
    )
