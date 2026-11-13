"""Small web app: type, record your voice, or upload images; get CMYK PDF/SVG back.

Run:  designer serve        (then open http://127.0.0.1:8000)
Set DESIGNER_TOKEN to require a password (mandatory when listening on a public address).
"""
from __future__ import annotations

import hmac
import os
import re
import shutil
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import pipeline, project
from .cli import _load_env

_load_env()
app = FastAPI(title="designer-ai")
_bearer = HTTPBearer(auto_error=False)
STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 25 * 1024 * 1024
_FILE_RE = re.compile(r"^design_v\d{3}\.(pdf|svg|png|json)$")
_SLUG_RE = re.compile(r"^[a-z0-9_-]{1,60}$")


def _auth(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    token = os.environ.get("DESIGNER_TOKEN")
    if not token:
        return
    if cred is None or not hmac.compare_digest(cred.credentials, token):
        raise HTTPException(status_code=401, detail="Wrong or missing token.")


async def _save_upload(up: UploadFile, folder: Path) -> Path:
    suffix = Path(up.filename or "").suffix.lower()[:8]
    if not re.fullmatch(r"\.[a-z0-9]{1,6}", suffix or ""):
        suffix = ".bin"
    dest = folder / f"{len(list(folder.iterdir())):03d}{suffix}"
    size = 0
    with open(dest, "wb") as f:
        while chunk := await up.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD:
                raise HTTPException(status_code=413, detail="File too large (25 MB max).")
            f.write(chunk)
    return dest


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.post("/api/design", dependencies=[Depends(_auth)])
async def api_design(
    text: str = Form(""),
    project_name: str = Form(""),
    size: str = Form(""),
    new: bool = Form(False),
    refine: int = Form(1),
    language: str = Form(""),
    audio: list[UploadFile] = File(default=[]),
    images: list[UploadFile] = File(default=[]),
):
    tmp = Path(tempfile.mkdtemp(prefix="designer_"))
    try:
        (tmp / "a").mkdir()
        (tmp / "i").mkdir()
        audio_paths = [await _save_upload(u, tmp / "a") for u in audio if u.filename]
        image_paths = [await _save_upload(u, tmp / "i") for u in images if u.filename]
        try:
            res = await run_in_threadpool(
                pipeline.run,
                instruction=text, audio=audio_paths, images=image_paths,
                project_name=project_name.strip() or None, new=new, size=size.strip() or None,
                refine=max(0, min(refine, 3)), language=language.strip() or None,
            )
        except (ValueError, RuntimeError) as e:
            raise HTTPException(status_code=400, detail=str(e))
        urls = {k: f"/files/{res.project}/{Path(v).name}" for k, v in res.files.items()}
        return {
            "project": res.project, "version": res.version, "size": res.report.summary(),
            "files": urls, "notes": res.notes, "warnings": res.report.warnings,
            "transcripts": res.transcripts,
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@app.get("/files/{proj}/{name}")
def get_file(proj: str, name: str, token: str = "", cred: HTTPAuthorizationCredentials | None = Depends(_bearer)):
    expected = os.environ.get("DESIGNER_TOKEN")
    if expected:
        given = cred.credentials if cred else token
        if not hmac.compare_digest(given or "", expected):
            raise HTTPException(status_code=401, detail="Wrong or missing token.")
    if not _SLUG_RE.match(proj) or not _FILE_RE.match(name):
        raise HTTPException(status_code=404)
    path = project.home() / proj / name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)
