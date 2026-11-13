"""Project folders with numbered versions, so every change ("make the logo bigger") builds on the last design."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

_VER = re.compile(r"^design_v(\d{3})\.json$")


def home() -> Path:
    return Path(os.environ.get("DESIGNER_HOME", "designs")).expanduser()


def slugify(name: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-").lower()
    return s[:60] or "design"


def new_project_name() -> str:
    return "design-" + time.strftime("%Y%m%d-%H%M%S")


def project_dir(name: str, create: bool = True) -> Path:
    d = home() / slugify(name)
    if create:
        (d / "assets").mkdir(parents=True, exist_ok=True)
    return d


def versions(d: Path) -> list[int]:
    if not d.exists():
        return []
    return sorted(int(m.group(1)) for f in d.iterdir() if (m := _VER.match(f.name)))


def load_latest(d: Path) -> tuple[int, dict] | None:
    vs = versions(d)
    if not vs:
        return None
    v = vs[-1]
    return v, json.loads((d / f"design_v{v:03d}.json").read_text(encoding="utf-8"))


def load_version(d: Path, v: int) -> dict:
    return json.loads((d / f"design_v{v:03d}.json").read_text(encoding="utf-8"))


def stem(d: Path, v: int) -> Path:
    return d / f"design_v{v:03d}"


def log_history(d: Path, version: int, instruction: str) -> None:
    with open(d / "history.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"version": version, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "instruction": instruction},
                           ensure_ascii=False) + "\n")


def safe_asset_name(name: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).name) or "file"
    return base[:80]
