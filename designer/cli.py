"""Command line interface.

  designer make "visiting card 3.5x2 in, navy blue, name Ali Khan" --project ali-card
  designer make --audio note.m4a --image reference.png --project poster --size "24x36 in"
  designer make --project ali-card "logo bigger, make the orange line thicker"      # revision
  designer render examples/business_card.json                                          # no AI
  designer fonts
  designer serve
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import __version__


def _load_env() -> None:
    for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
        env = base / ".env"
        if env.is_file():
            for line in env.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _formats(s: str) -> set[str]:
    f = {x.strip().lower() for x in s.split(",") if x.strip()}
    bad = f - {"pdf", "svg", "png"}
    if bad:
        raise argparse.ArgumentTypeError(f"unknown format(s): {', '.join(sorted(bad))} (use pdf,svg,png)")
    return f | {"pdf"}


def _print_result(res) -> None:
    r = res.report
    print(f"\nProject : {res.project}  (version {res.version})")
    print(f"Size    : {r.summary()}")
    for kind in ("pdf", "svg", "png", "json"):
        if kind in res.files:
            print(f"{kind.upper():8}: {res.files[kind]}")
    if res.notes:
        print(f"\nNotes   : {res.notes}")
    for w in r.warnings:
        print(f"Warning : {w}")


def cmd_make(a) -> int:
    from . import pipeline

    res = pipeline.run(
        instruction=" ".join(a.text), audio=a.audio, images=a.image, project_name=a.project, new=a.new,
        size=a.size, formats=a.formats, refine=a.refine, model=a.model, language=a.language,
        whisper_model=a.whisper_model, on_status=lambda m: print(m, file=sys.stderr),
    )
    _print_result(res)
    return 0


def cmd_render(a) -> int:
    from . import pipeline
    from .spec import normalize_spec
    from .units import parse_size

    src = Path(a.spec)
    spec = json.loads(src.read_text(encoding="utf-8"))
    if a.size:
        spec["size"] = parse_size(a.size)
    spec = normalize_spec(spec)
    out = Path(a.out) if a.out else src.parent
    out.mkdir(parents=True, exist_ok=True)
    files, report = pipeline.export(spec, out / src.stem, a.formats, assets_dir=src.parent / "assets")
    print(f"Size: {report.summary()}")
    for k, v in files.items():
        print(f"{k.upper():5}: {v}")
    for w in report.warnings:
        print(f"Warning: {w}")
    return 0


def cmd_fonts(_a) -> int:
    from .fonts import list_families

    for name in list_families(1000):
        print(name)
    return 0


def cmd_serve(a) -> int:
    try:
        import uvicorn
    except ImportError:
        print("Install the web extras first: pip install fastapi uvicorn python-multipart", file=sys.stderr)
        return 1
    if a.host not in ("127.0.0.1", "localhost") and not os.environ.get("DESIGNER_TOKEN"):
        print("Refusing to listen on a public address without DESIGNER_TOKEN set (it would expose your API key's credits).",
              file=sys.stderr)
        return 1
    uvicorn.run("designer.server:app", host=a.host, port=a.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="designer", description="Voice / text / image -> print-ready CMYK vector design.")
    p.add_argument("--version", action="version", version=f"designer-ai {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    m = sub.add_parser("make", help="create or revise a design")
    m.add_argument("text", nargs="*", help="what to design / what to change (any language)")
    m.add_argument("--audio", "-a", action="append", default=[], help="voice recording(s), any language")
    m.add_argument("--image", "-i", action="append", default=[], help="reference image(s)")
    m.add_argument("--project", "-p", help="project name; repeated calls with the same name revise the latest version")
    m.add_argument("--new", action="store_true", help="start from scratch even if the project already has versions")
    m.add_argument("--size", "-s", help="force final size, e.g. '3.5x2in', '90x55mm', '10ft x 4ft', 'A4'")
    m.add_argument("--formats", type=_formats, default={"pdf", "svg", "png"}, help="comma list: pdf,svg,png (default all)")
    m.add_argument("--refine", type=int, default=1, help="visual self-check passes (0 = off, default 1)")
    m.add_argument("--model", help="Claude model id (default from DESIGNER_MODEL or claude-sonnet-5-5)")
    m.add_argument("--language", help="voice language hint, e.g. ur, hi, pa, ar, en (default: auto-detect)")
    m.add_argument("--whisper-model", help="tiny|base|small|medium|large-v3 (bigger = more accurate for Urdu)")
    m.set_defaults(fn=cmd_make)

    r = sub.add_parser("render", help="render an existing spec .json (no AI)")
    r.add_argument("spec")
    r.add_argument("--out", "-o")
    r.add_argument("--size", "-s")
    r.add_argument("--formats", type=_formats, default={"pdf", "svg", "png"})
    r.set_defaults(fn=cmd_render)

    f = sub.add_parser("fonts", help="list usable fonts")
    f.set_defaults(fn=cmd_fonts)

    s = sub.add_parser("serve", help="start the web app (record voice, upload images)")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    _load_env()
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except KeyboardInterrupt:
        return 130
    except Exception as e:  # keep the CLI friendly; full traceback with DESIGNER_DEBUG=1
        if os.environ.get("DESIGNER_DEBUG"):
            raise
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
