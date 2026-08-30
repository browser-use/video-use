import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from helpers import render


class SubtitleCapabilityTests(unittest.TestCase):
    def test_filter_listing_requires_exact_subtitles_entry(self) -> None:
        listing = """Filters:
  T.. drawtext          V->V       Draw text on top of video frames.
  ... ass               V->V       Render ASS subtitles onto input video.
"""

        self.assertFalse(render.has_subtitles_filter(listing))

        listing += (
            "  .. subtitles        V->V       Render text subtitles onto input video "
            "using the libass library.\n"
        )
        self.assertTrue(render.has_subtitles_filter(listing))

    def test_failed_probe_exits_with_actionable_diagnostic(self) -> None:
        with (
            mock.patch.object(
                render.subprocess,
                "run",
                side_effect=subprocess.CalledProcessError(1, ["ffmpeg", "-filters"]),
            ),
            self.assertRaises(SystemExit) as raised,
        ):
            render.require_subtitles_filter()

        message = str(raised.exception)
        self.assertIn("libass-enabled ffmpeg", message)
        self.assertIn("brew install ffmpeg-full", message)
        self.assertIn('export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"', message)
        self.assertIn("ffmpeg -hide_banner -filters", message)

    def test_successful_probe_without_subtitles_filter_uses_same_diagnostic(
        self,
    ) -> None:
        probe = subprocess.CompletedProcess(
            ["ffmpeg", "-filters"],
            0,
            stdout="  T.. drawtext          V->V       Draw text on video.\n",
            stderr="",
        )
        with (
            mock.patch.object(render.subprocess, "run", return_value=probe),
            self.assertRaises(SystemExit) as raised,
        ):
            render.require_subtitles_filter()

        self.assertEqual(str(raised.exception), render.SUBTITLES_FILTER_ERROR)


class SubtitlePreflightTests(unittest.TestCase):
    def _write_edl(self, directory: Path, **extra: object) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        edl = {"sources": {}, "ranges": [], **extra}
        path = directory / "edl.json"
        path.write_text(json.dumps(edl))
        return path

    def _run_main_until_extraction(self, edl_path: Path, *extra_args: str) -> None:
        argv = [
            "render.py",
            str(edl_path),
            "-o",
            str(edl_path.parent / "out.mp4"),
            *extra_args,
        ]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(
                render,
                "extract_all_segments",
                side_effect=RuntimeError("extraction reached"),
            ),
            self.assertRaisesRegex(RuntimeError, "extraction reached"),
        ):
            render.main()

    def test_build_subtitles_requires_filter_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            edl_path = self._write_edl(Path(temp_dir))
            argv = [
                "render.py",
                str(edl_path),
                "-o",
                str(Path(temp_dir) / "out.mp4"),
                "--build-subtitles",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    render,
                    "require_subtitles_filter",
                    side_effect=SystemExit("missing"),
                ),
                mock.patch.object(render, "extract_all_segments") as extract,
                self.assertRaisesRegex(SystemExit, "missing"),
            ):
                render.main()
            extract.assert_not_called()

    def test_existing_edl_subtitles_require_filter_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "captions.srt").write_text("captions")
            edl_path = self._write_edl(directory, subtitles="captions.srt")
            argv = ["render.py", str(edl_path), "-o", str(directory / "out.mp4")]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    render,
                    "require_subtitles_filter",
                    side_effect=SystemExit("missing"),
                ),
                mock.patch.object(render, "extract_all_segments") as extract,
                self.assertRaisesRegex(SystemExit, "missing"),
            ):
                render.main()
            extract.assert_not_called()

    def test_subtitle_free_modes_skip_filter_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "captions.srt").write_text("captions")

            cases = [
                (
                    self._write_edl(
                        directory / "disabled", subtitles="../captions.srt"
                    ),
                    ("--no-subtitles",),
                ),
                (self._write_edl(directory / "none"), ()),
            ]
            for edl_path, args in cases:
                with self.subTest(
                    args=args,
                    subtitles=json.loads(edl_path.read_text()).get("subtitles"),
                ):
                    with (
                        mock.patch.object(
                            render, "require_subtitles_filter"
                        ) as require,
                        redirect_stdout(StringIO()),
                    ):
                        self._run_main_until_extraction(edl_path, *args)
                    require.assert_not_called()

    def test_missing_edl_subtitles_warns_and_skips_filter_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            edl_path = self._write_edl(Path(temp_dir), subtitles="missing.srt")
            output = StringIO()
            with (
                mock.patch.object(render, "require_subtitles_filter") as require,
                redirect_stdout(output),
            ):
                self._run_main_until_extraction(edl_path)

        require.assert_not_called()
        self.assertIn(
            "warning: subtitles path in EDL does not exist:", output.getvalue()
        )
        self.assertIn("missing.srt", output.getvalue())

    def test_non_string_subtitle_font_exits_cleanly_before_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            edl_path = self._write_edl(Path(temp_dir), subtitle_font=True)
            argv = [
                "render.py",
                str(edl_path),
                "-o",
                str(Path(temp_dir) / "out.mp4"),
                "--build-subtitles",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(render, "require_subtitles_filter"),
                mock.patch.object(render, "extract_all_segments") as extract,
                self.assertRaisesRegex(
                    SystemExit, "invalid subtitle_font in edl: .*must be a string"
                ),
            ):
                render.main()

            extract.assert_not_called()


class SubtitleStyleTests(unittest.TestCase):
    def test_default_style_is_unchanged(self) -> None:
        self.assertEqual(
            render.build_subtitle_force_style(),
            "FontName=Helvetica,FontSize=18,Bold=1,"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H00000000,"
            "BorderStyle=1,Outline=2,Shadow=0,Alignment=2,MarginV=90",
        )

    def test_font_override_changes_only_font_name_in_final_filter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            subtitles = directory / "captions' set:zh.srt"
            subtitles.write_text("captions")
            overlays = [
                {"file": "overlay.mp4", "start_in_output": 1.25, "duration": 2.5}
            ]
            style = render.build_subtitle_force_style("Heiti SC")

            with mock.patch.object(render.subprocess, "run") as run:
                render.build_final_composite(
                    directory / "base.mp4",
                    overlays,
                    subtitles,
                    directory / "out.mp4",
                    directory,
                    subtitle_force_style=style,
                )

        command = run.call_args.args[0]
        filter_complex = command[command.index("-filter_complex") + 1]
        escaped_path = str(subtitles.resolve()).replace(":", r"\:").replace("'", r"\'")
        self.assertIn("setpts=PTS-STARTPTS+1.25/TB", filter_complex)
        self.assertIn("overlay=enable='between(t,1.250,3.750)'", filter_complex)
        self.assertIn(
            f"subtitles='{escaped_path}':force_style='{style}'[outv]", filter_complex
        )
        self.assertLess(
            filter_complex.index("overlay="), filter_complex.index("subtitles=")
        )
        self.assertEqual(
            style, render.SUB_FORCE_STYLE.replace("Helvetica", "Heiti SC", 1)
        )

    def test_font_override_rejects_force_style_delimiters(self) -> None:
        for font_name in ("Helvetica,FontSize=72", "Reader's Font", r"Font\Name"):
            with self.subTest(font_name=font_name):
                with self.assertRaisesRegex(ValueError, "subtitle_font"):
                    render.build_subtitle_force_style(font_name)

    def test_font_override_rejects_non_string_values(self) -> None:
        for font_name in (True, 42, ["Helvetica"]):
            with self.subTest(font_name=font_name):
                with self.assertRaisesRegex(ValueError, "must be a string"):
                    render.build_subtitle_force_style(font_name)

    def test_main_passes_edl_font_override_to_compositing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            (directory / "captions.srt").write_text("captions")
            edl_path = directory / "edl.json"
            edl_path.write_text(
                json.dumps(
                    {
                        "sources": {},
                        "ranges": [],
                        "subtitles": "captions.srt",
                        "subtitle_font": "Heiti SC",
                    }
                )
            )
            output_path = directory / "out.mp4"
            argv = [
                "render.py",
                str(edl_path),
                "-o",
                str(output_path),
                "--no-loudnorm",
            ]

            def write_composite(*args: object, **kwargs: object) -> None:
                Path(args[3]).write_bytes(b"video")

            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(render, "require_subtitles_filter"),
                mock.patch.object(render, "extract_all_segments", return_value=[]),
                mock.patch.object(render, "concat_segments"),
                mock.patch.object(
                    render, "build_final_composite", side_effect=write_composite
                ) as composite,
                redirect_stdout(StringIO()),
            ):
                render.main()

        self.assertEqual(
            composite.call_args.kwargs["subtitle_force_style"],
            render.build_subtitle_force_style("Heiti SC"),
        )


if __name__ == "__main__":
    unittest.main()
