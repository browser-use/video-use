"""tests for the preview_scene script
it loads the script from its path and checks error handling and one real low quality render
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image


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


# endpoints are decoded once and reused for the sheet rather than extracted again
def test_review_decodes_five_not_seven_frames(tmp_path, monkeypatch):
    calls = []

    # synthesize stills while recording requested decode timestamps
    def extract(video, output, timestamp_s, **kwargs):
        calls.append(timestamp_s)
        Image.new('RGB', (640, 360)).save(output)

    monkeypatch.setattr(preview_scene, '_extract_frame', extract)
    result = preview_scene.build_review_frames(tmp_path / 'v.mp4', tmp_path,
        {'duration_s': 2, 'frame_rate': 30}, ffmpeg_bin='unused', runner=None, timeout_s=10)
    assert len(calls) == 5 and len(set(calls)) == 5
    assert Image.open(result['initial_frame']).size == (640, 360)


# options reject invalid clocks before invoking any executable
@pytest.mark.parametrize('kwargs', [{'fps': 0}, {'fps': float('nan')},
    {'fps': float('inf')}, {'quality': 'ultra'}, {'timeout_s': -1}])
def test_invalid_render_options(tmp_path, kwargs):
    with pytest.raises(preview_scene.PreviewError):
        preview_scene.render_scene(tmp_path / 'absent.py', 'Demo', **kwargs)


# explicit delivery clocks and quality are passed to Manim without changing defaults
def test_explicit_clock_and_quality(tmp_path):
    script = _scene_script(tmp_path / 'edit' / 'scene.py')
    commands = []

    # stop after capturing the exact command passed to Manim
    def runner(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 1, '', 'intentional stop')

    with pytest.raises(preview_scene.PreviewError, match='intentional stop'):
        preview_scene.render_scene(script, 'Demo', fps=30, quality='high',
            runner=runner, manim_bin='/usr/bin/true', ffmpeg_bin='/usr/bin/true', ffprobe_bin='/usr/bin/true')
    assert '-qh' in commands[0]
    assert commands[0][commands[0].index('--fps') + 1] == '30'


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


# an older successful preview cannot satisfy a later render that writes no media
def test_preview_rejects_old_video_and_preserves_previous_report(tmp_path, monkeypatch):
    script = _scene_script(tmp_path / "edit" / "scene.py")
    attempts = []

    # create output on the first invocation and leave the second invocation empty
    def runner(command, **kwargs):
        media = Path(command[command.index("--media_dir") + 1])
        attempts.append(media)
        if len(attempts) == 1:
            media.mkdir(parents=True)
            (media / "Demo.mp4").write_bytes(b"first successful render")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(preview_scene, "probe_video", lambda *args, **kwargs:
        {"duration_s": 1, "width": 640, "height": 360, "frame_rate": 30})
    monkeypatch.setattr(preview_scene, "build_review_frames", lambda *args, **kwargs: {})
    options = dict(runner=runner, manim_bin="/usr/bin/true",
                   ffmpeg_bin="/usr/bin/true", ffprobe_bin="/usr/bin/true")
    report = preview_scene.render_scene(script, "Demo", **options)
    old_report = Path(report["report"]).read_bytes()
    with pytest.raises(preview_scene.PreviewError, match="produced no video"):
        preview_scene.render_scene(script, "Demo", **options)
    assert attempts[0] != attempts[1]
    assert Path(report["video"]).read_bytes() == b"first successful render"
    assert Path(report["report"]).read_bytes() == old_report
    assert not (attempts[1].parent / "report.json").exists()


# scripts with matching names retain separate source identities under one project
def test_preview_separates_equal_source_names(tmp_path):
    scripts = [_scene_script(tmp_path / "edit" / folder / "scene.py")
               for folder in ("first", "second")]
    attempts = []

    # inspect the attempted destination before any media work occurs
    def runner(command, **kwargs):
        attempts.append(Path(command[command.index("--media_dir") + 1]))
        return subprocess.CompletedProcess(command, 0, "", "")

    for script in scripts:
        with pytest.raises(preview_scene.PreviewError, match="produced no video"):
            preview_scene.render_scene(script, "Demo", runner=runner,
                manim_bin="/usr/bin/true", ffmpeg_bin="/usr/bin/true", ffprobe_bin="/usr/bin/true")
    assert attempts[0].parents[2] != attempts[1].parents[2]


# a relative executable stays valid after the renderer changes its working directory
def test_preview_resolves_relative_executables(tmp_path, monkeypatch):
    executable = tmp_path / "bin" / "renderer"
    executable.parent.mkdir()
    executable.write_text("#!/bin/sh\nexit 0\n")
    executable.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    assert preview_scene.require_executable("bin/renderer", purpose="renderer") == str(executable)


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


# preview renders load the same project configuration and relative assets as production
def test_review_project_working_directory(tmp_path):
    script = _scene_script(tmp_path / 'project' / 'edit' / 'animations' / 'scene.py')
    calls = []
    # stop after observing the requested working directory
    def runner(command, **kwargs):
        calls.append(kwargs['cwd'])
        return subprocess.CompletedProcess(command, 1, '', 'intentional stop')
    with pytest.raises(preview_scene.PreviewError, match='intentional stop'):
        preview_scene.render_scene(script, 'Demo', runner=runner, manim_bin='/usr/bin/true', ffmpeg_bin='/usr/bin/true', ffprobe_bin='/usr/bin/true')
    assert calls == [str(tmp_path / 'project')]
