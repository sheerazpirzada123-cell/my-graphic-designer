"""Claude -> design spec (with vision input and a validate-and-retry loop)."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

from PIL import Image

from .schema import TOOL, system_prompt
from .spec import SpecError, normalize_spec

DEFAULT_MODEL = os.environ.get("DESIGNER_MODEL", "claude-sonnet-5-5")
MAX_IMAGE_SIDE = 1568
MAX_TOKENS = 16000


class LLMError(RuntimeError):
    pass


def _client():
    try:
        import anthropic
    except ImportError as e:  # pragma: no cover
        raise LLMError("The 'anthropic' package is not installed. Run: pip install -r requirements.txt") from e
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise LLMError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key.")
    return anthropic.Anthropic()


def image_block(path: str | Path) -> tuple[dict, tuple[int, int]]:
    """Load an image, flatten transparency onto white, downscale, return (content block, original size)."""
    img = Image.open(path)
    orig = img.size
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, (255, 255, 255))
        bg.paste(img, mask=img.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    data = base64.standard_b64encode(buf.getvalue()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}}, orig


def _call(messages: list[dict], model: str, max_retries: int = 2) -> dict:
    """Call the model until it returns a valid spec (invalid specs are sent back with the error)."""
    client = _client()
    system = system_prompt()
    last_err = ""
    for attempt in range(max_retries + 1):
        resp = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[TOOL],
            tool_choice={"type": "tool", "name": TOOL["name"]},
            messages=messages,
        )
        tool_use = next((b for b in resp.content if b.type == "tool_use" and b.name == TOOL["name"]), None)
        if resp.stop_reason == "max_tokens" or tool_use is None:
            raise LLMError("The model did not finish the design (response cut off). Try a simpler request.")
        try:
            return normalize_spec(tool_use.input)
        except SpecError as e:
            last_err = str(e)
            if attempt == max_retries:
                break
            messages = messages + [
                {"role": "assistant", "content": resp.content},
                {"role": "user", "content": [{
                    "type": "tool_result", "tool_use_id": tool_use.id, "is_error": True,
                    "content": f"The spec is invalid: {last_err}\nCall create_design again with the full corrected spec.",
                }]},
            ]
    raise LLMError(f"Could not get a valid design from the model: {last_err}")


def generate_spec(
    instruction: str,
    images: list[str | Path] | None = None,
    base_spec: dict | None = None,
    size_hint: dict | None = None,
    asset_names: list[str] | None = None,
    model: str | None = None,
) -> dict:
    content: list[dict] = []
    for i, p in enumerate(images or [], 1):
        block, (w, h) = image_block(p)
        content.append({"type": "text", "text": f"Reference image {i} ({Path(p).name}, {w}x{h}px, aspect {w / h:.3f}):"})
        content.append(block)

    parts: list[str] = []
    if base_spec:
        parts.append("CURRENT DESIGN SPEC (modify this):\n" + json.dumps(base_spec, ensure_ascii=False))
    parts.append("USER INSTRUCTION:\n" + (instruction.strip() or "(none - use the reference image(s))"))
    if size_hint:
        parts.append(
            f"REQUIRED FINAL SIZE: {size_hint['width']:g} x {size_hint['height']:g} {size_hint['unit']} "
            "(use exactly this in size)."
        )
    if asset_names:
        parts.append("Supplied files usable with an image element only if the user asks to place them as-is: " + ", ".join(asset_names))
    content.append({"type": "text", "text": "\n\n".join(parts)})
    return _call([{"role": "user", "content": content}], model or DEFAULT_MODEL)


def refine_spec(
    spec: dict,
    preview_png: str | Path,
    instruction: str,
    images: list[str | Path] | None = None,
    model: str | None = None,
) -> dict:
    """Show the model a render of its own design and let it fix problems (visual self-check)."""
    content: list[dict] = []
    for i, p in enumerate(images or [], 1):
        block, _ = image_block(p)
        content.append({"type": "text", "text": f"Reference image {i}:"})
        content.append(block)
    block, _ = image_block(preview_png)
    content.append({"type": "text", "text": "Render of the CURRENT design:"})
    content.append(block)
    content.append({"type": "text", "text": (
        "Review the render against the user's request"
        + (" and the reference image(s)" if images else "")
        + ". Fix any real problems: text overlapping or clipped, text outside safe margins, wrong proportions or "
        "positions versus the reference, poor contrast, unbalanced spacing, missing requested elements. "
        "Do not change things that are already right. If the design is already correct, return the same spec unchanged.\n\n"
        f"USER REQUEST:\n{instruction.strip() or '(see reference image)'}\n\n"
        "CURRENT SPEC:\n" + json.dumps(spec, ensure_ascii=False)
    )})
    return _call([{"role": "user", "content": content}], model or DEFAULT_MODEL)
