r"""Regression tests for the Windows cross-platform fixes in the helpers.

Bug 1 (ffmpeg path): grade.py --analyze crashed on Windows because the
signalstats metadata filter embedded a temp path (e.g. C:\Users\me\Temp\stats.txt),
and the ':' and '\' in that path collide with ffmpeg filtergraph delimiters, so
ffmpeg exited with an error. Fix: _metadata_filter_target() returns the file's
directory (for ffmpeg's cwd) and the bare filename (for the filter), so no path
separator or drive colon reaches the filtergraph parser.

Bug 2 (cp1252 print crash): status print() calls emit non-ASCII glyphs (U+2192
arrow, U+2014 em-dash) which raise UnicodeEncodeError on a Windows cp1252 console.
Fix: each helper reconfigures stdout/stderr to UTF-8 at import.

These tests are written to pass identically on Windows and on Linux CI.
"""
import subprocess
import sys
import unittest
from pathlib import Path

HELPERS_DIR = Path(__file__).resolve().parent.parent / "helpers"
sys.path.insert(0, str(HELPERS_DIR))
import grade  # noqa: E402


class MetadataFilterTargetTest(unittest.TestCase):
    """The filter value must be a bare filename (no ':' or path separator) for
    BOTH Windows and POSIX inputs, regardless of which OS runs the test."""

    def test_windows_path_yields_bare_name(self):
        cwd, name = grade._metadata_filter_target(r"C:\Users\me\AppData\Local\Temp\stats.txt")
        self.assertEqual(name, "stats.txt")
        self.assertEqual(cwd, r"C:\Users\me\AppData\Local\Temp")
        for ch in (":", "/", "\\"):
            self.assertNotIn(ch, name)

    def test_posix_path_yields_bare_name(self):
        cwd, name = grade._metadata_filter_target("/var/folders/xy/stats.txt")
        self.assertEqual(name, "stats.txt")
        self.assertEqual(cwd, "/var/folders/xy")

    def test_bare_filename_has_no_dir(self):
        cwd, name = grade._metadata_filter_target("stats.txt")
        self.assertEqual(name, "stats.txt")
        self.assertEqual(cwd, ".")


class Utf8ConsoleTest(unittest.TestCase):
    """Regression for the cp1252 print crash. Runs in a subprocess so we can
    force a cp1252 stdout without disturbing the test runner's own streams."""

    def test_arrow_glyphs_survive_cp1252_after_reconfigure(self):
        # Force the Windows-default cp1252 stdout, apply the same reconfigure the
        # helpers do at import, then print the exact glyphs that crashed. Without
        # the reconfigure line this raises UnicodeEncodeError; with it, exit 0.
        code = (
            "import sys, io;"
            "sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='cp1252');"
            "sys.stdout.reconfigure(encoding='utf-8');"
            "print('grading a → b — c')"
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr.decode("utf-8", "replace"))

    def test_helpers_force_utf8_stdout_on_import(self):
        # After import, each patched helper must leave stdout UTF-8-capable, so
        # an accidental deletion of the reconfigure block is caught here.
        for mod in ("grade", "pack_transcripts"):
            code = (
                f"import sys; sys.path.insert(0, r'{HELPERS_DIR}');"
                f"import {mod};"
                "enc=(sys.stdout.encoding or '').lower().replace('-', '');"
                "sys.exit(0 if 'utf8' in enc else 1)"
            )
            r = subprocess.run([sys.executable, "-c", code], capture_output=True)
            self.assertEqual(
                r.returncode, 0,
                f"{mod}: stdout not UTF-8 after import; {r.stderr.decode('utf-8', 'replace')}",
            )


if __name__ == "__main__":
    unittest.main()
