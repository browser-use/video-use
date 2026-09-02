"""tests for the preview_scene script
it loads the script from its path and checks error handling and one real low quality render
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).parents[1]
    / "skills"
    / "manim-video"
    / "scripts"
    / "preview_scene.py"
)
# load the script as a module directly from its file path
SPEC = importlib.util.spec_from_file_location("preview_scene", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
preview_scene = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(preview_scene)


# write a tiny manim script with one scene class
def _scene_script(path: Path, name: str = "Demo") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from manim import *\n"
        f"class {name}(Scene):\n"
        "    def construct(self):\n"
        "        self.next_section('payoff')\n"
        "        self.add(Dot())\n"
        "        self.wait(0.2)\n",
        encoding="utf-8",
    )
    return path


# an unknown scene class fails before any render
def test_preview_rejects_missing_scene_before_render(tmp_path: Path) -> None:
    script = _scene_script(tmp_path / "edit" / "scene.py")

    with pytest.raises(preview_scene.PreviewError, match="was not found"):
        preview_scene.render_scene(script, "Missing")


# a missing manim executable fails with a clear message
def test_preview_fails_clearly_when_manim_is_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    script = _scene_script(tmp_path / "edit" / "scene.py")
    monkeypatch.setattr(preview_scene.shutil, "which", lambda _name: None)

    with pytest.raises(preview_scene.PreviewError, match="Manim is unavailable"):
        preview_scene.render_scene(script, "Demo")


# a non zero render exit code surfaces the stderr text
def test_preview_reports_render_failure(tmp_path: Path) -> None:
    script = _scene_script(tmp_path / "edit" / "scene.py")

    # stand in for the manim subprocess that fails with an error message
    def failed_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 2, stdout="", stderr="render exploded")

    with pytest.raises(preview_scene.PreviewError, match="render exploded"):
        preview_scene.render_scene(
            script,
            "Demo",
            manim_bin=shutil.which("true") or "/usr/bin/true",
            ffmpeg_bin=shutil.which("true") or "/usr/bin/true",
            ffprobe_bin=shutil.which("true") or "/usr/bin/true",
            runner=failed_runner,
        )


# a successful run with no output video is rejected
def test_preview_rejects_success_without_expected_artifacts(tmp_path: Path) -> None:
    script = _scene_script(tmp_path / "edit" / "scene.py")

    # stand in for the manim subprocess that succeeds without writing anything
    def successful_runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout="", stderr="")

    with pytest.raises(preview_scene.PreviewError, match="produced no video"):
        preview_scene.render_scene(
            script,
            "Demo",
            manim_bin=shutil.which("true") or "/usr/bin/true",
            ffmpeg_bin=shutil.which("true") or "/usr/bin/true",
            ffprobe_bin=shutil.which("true") or "/usr/bin/true",
            runner=successful_runner,
        )


# a real render lands under edit verify with the expected metadata and artifacts
def test_preview_real_manim_render_stays_under_edit_verify(tmp_path: Path) -> None:
    pytest.importorskip("manim")
    manim = shutil.which("manim")
    if manim is None:
        pytest.skip("Manim executable is not installed")
    edit_dir = tmp_path / "project" / "edit"
    script = _scene_script(edit_dir / "animations" / "scene.py", "PreviewSmoke")

    report = preview_scene.render_scene(
        script,
        "PreviewSmoke",
        manim_bin=manim,
        timeout_s=180,
    )

    assert report["status"] == "ok"
    assert report["width"] == 854
    assert report["height"] == 480
    assert report["frame_rate"] == pytest.approx(15)
    assert report["sections"]
    for key in ("video", "initial_frame", "final_frame", "contact_sheet", "report"):
        artifact = Path(report[key]).resolve()
        assert artifact.is_file()
        assert artifact.is_relative_to((edit_dir / "verify").resolve())
