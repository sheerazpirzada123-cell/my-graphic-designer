import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from designer import llm, pipeline, project, transcribe  # noqa: E402
from designer.cli import main  # noqa: E402

EXAMPLES = Path(__file__).parent.parent / "examples"


def card_spec(**kw):
    s = json.loads((EXAMPLES / "business_card.json").read_text())
    s.update(kw)
    return s


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DESIGNER_HOME", str(tmp_path / "designs"))
    return tmp_path


def test_make_then_revise_builds_on_latest_version(home, monkeypatch):
    calls = []

    def fake_generate(instruction, images=None, base_spec=None, size_hint=None, asset_names=None, model=None):
        calls.append({"instruction": instruction, "base": base_spec, "size_hint": size_hint})
        return llm.normalize_spec(card_spec(title=f"v{len(calls)}"))

    monkeypatch.setattr(llm, "generate_spec", fake_generate)
    monkeypatch.setattr(llm, "refine_spec", lambda spec, *a, **k: spec)

    r1 = pipeline.run("visiting card", project_name="ali", on_status=lambda m: None)
    assert r1.version == 1 and Path(r1.files["pdf"]).is_file() and Path(r1.files["svg"]).is_file()
    assert calls[0]["base"] is None

    r2 = pipeline.run("logo bigger", project_name="ali")
    assert r2.version == 2
    assert calls[1]["base"]["title"] == "v1"  # revision is based on the previous version

    r3 = pipeline.run("start over", project_name="ali", new=True)
    assert r3.version == 3 and calls[2]["base"] is None

    hist = (project.project_dir("ali") / "history.jsonl").read_text().splitlines()
    assert len(hist) == 3


def test_size_override_is_enforced(home, monkeypatch):
    monkeypatch.setattr(llm, "generate_spec", lambda *a, **k: llm.normalize_spec(card_spec()))
    monkeypatch.setattr(llm, "refine_spec", lambda spec, *a, **k: spec)
    res = pipeline.run("card", project_name="x", size="90x55mm", refine=0)
    assert res.spec["size"] == {"width": 90.0, "height": 55.0, "unit": "mm"}
    assert res.report.summary().startswith("90 x 55 mm")


def test_voice_text_and_image_are_combined(home, monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(transcribe, "transcribe", lambda path, language=None, model_size=None: ("naam Ali Khan likho", "ur"))

    def fake_generate(instruction, images=None, **k):
        seen["instruction"], seen["images"], seen["assets"] = instruction, images, k.get("asset_names")
        return llm.normalize_spec(card_spec())

    monkeypatch.setattr(llm, "generate_spec", fake_generate)
    from PIL import Image
    ref = tmp_path / "ref one.png"
    Image.new("RGB", (50, 30), "navy").save(ref)
    audio = tmp_path / "note.m4a"
    audio.write_bytes(b"x")

    res = pipeline.run("blue card", audio=[audio], images=[ref], project_name="v", refine=0)
    assert "blue card" in seen["instruction"] and "naam Ali Khan likho" in seen["instruction"]
    assert seen["assets"] == ["ref_one.png"]
    assert res.transcripts == ["naam Ali Khan likho"]


def test_refine_pass_replaces_spec_and_keeps_size(home, monkeypatch):
    from designer.preview import have_preview

    if not have_preview():
        pytest.skip("pdftoppm not installed")
    monkeypatch.setattr(llm, "generate_spec", lambda *a, **k: llm.normalize_spec(card_spec(title="first")))
    monkeypatch.setattr(llm, "refine_spec", lambda spec, png, *a, **k: llm.normalize_spec(
        {**spec, "title": "fixed", "size": {"width": 1, "height": 1, "unit": "in"}}))
    res = pipeline.run("x", project_name="r", size="3.5x2in", refine=1)
    assert res.spec["title"] == "fixed"
    assert res.spec["size"] == {"width": 3.5, "height": 2.0, "unit": "in"}


def test_nothing_to_do_is_an_error(home):
    with pytest.raises(ValueError):
        pipeline.run("")


# ---------------------------------------------------------------- the LLM wrapper itself
def _fake_client(responses):
    it = iter(responses)
    calls = []

    def create(**kw):
        calls.append(kw)
        return next(it)

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


def _tool_resp(spec, id="tu_1", stop="tool_use"):
    block = SimpleNamespace(type="tool_use", name="create_design", input=spec, id=id)
    return SimpleNamespace(content=[block], stop_reason=stop)


def test_invalid_spec_is_sent_back_and_retried(monkeypatch):
    bad = {"size": {"width": 1, "height": 1, "unit": "in"}, "elements": [{"type": "rect", "x": 0}]}
    good = card_spec()
    client, calls = _fake_client([_tool_resp(bad), _tool_resp(good, id="tu_2")])
    monkeypatch.setattr(llm, "_client", lambda: client)
    spec = llm.generate_spec("card")
    assert spec["title"].startswith("Business card")
    assert len(calls) == 2
    retry_msgs = calls[1]["messages"]
    assert retry_msgs[-1]["content"][0]["type"] == "tool_result" and retry_msgs[-1]["content"][0]["is_error"]
    assert calls[0]["tool_choice"] == {"type": "tool", "name": "create_design"}


def test_gives_up_after_retries(monkeypatch):
    bad = {"size": {"width": 1, "height": 1, "unit": "in"}, "elements": [{"type": "nope"}]}
    client, _ = _fake_client([_tool_resp(bad, id=f"t{i}") for i in range(3)])
    monkeypatch.setattr(llm, "_client", lambda: client)
    with pytest.raises(llm.LLMError):
        llm.generate_spec("card")


def test_truncated_response_is_reported(monkeypatch):
    client, _ = _fake_client([_tool_resp(card_spec(), stop="max_tokens")])
    monkeypatch.setattr(llm, "_client", lambda: client)
    with pytest.raises(llm.LLMError):
        llm.generate_spec("card")


def test_image_and_base_spec_reach_the_model(monkeypatch, tmp_path):
    from PIL import Image

    img = tmp_path / "a.png"
    Image.new("RGBA", (3000, 1000), (255, 0, 0, 0)).save(img)
    client, calls = _fake_client([_tool_resp(card_spec())])
    monkeypatch.setattr(llm, "_client", lambda: client)
    llm.generate_spec("same but red", images=[img], base_spec={"title": "old"},
                      size_hint={"width": 3.5, "height": 2, "unit": "in"})
    content = calls[0]["messages"][0]["content"]
    types = [c["type"] for c in content]
    assert "image" in types
    text = " ".join(c["text"] for c in content if c["type"] == "text")
    assert "CURRENT DESIGN SPEC" in text and "REQUIRED FINAL SIZE: 3.5 x 2 in" in text
    block = next(c for c in content if c["type"] == "image")
    assert block["source"]["media_type"] == "image/jpeg"


def test_system_prompt_lists_fonts_and_rules():
    from designer.schema import system_prompt

    p = system_prompt()
    assert "CMYK" in p and "Helvetica" in p and "bleed" in p


# ---------------------------------------------------------------- CLI
def test_cli_render_without_ai(tmp_path, capsys):
    src = tmp_path / "card.json"
    src.write_text((EXAMPLES / "business_card.json").read_text())
    assert main(["render", str(src), "--size", "90x55mm", "--formats", "pdf,svg"]) == 0
    out = capsys.readouterr().out
    assert "90 x 55 mm" in out
    assert (tmp_path / "card.pdf").is_file() and (tmp_path / "card.svg").is_file()


def test_cli_rejects_bad_size(tmp_path, capsys):
    src = tmp_path / "card.json"
    src.write_text((EXAMPLES / "business_card.json").read_text())
    assert main(["render", str(src), "--size", "banana"]) == 1
