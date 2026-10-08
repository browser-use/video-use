"""tests for the illustration renderer covering argument parsing and input validation
external tools are patched away so nothing is installed or compiled during the run
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import render_illustration as ri


# checks the command line parser
class ParserTests(unittest.TestCase):
    # both subcommands parse their positional source and output options
    def test_parses_both_engines(self):
        parser = ri.build_parser()
        cetz = parser.parse_args(["cetz", "fig.typ", "-o", "fig.svg"])
        self.assertEqual((cetz.engine, cetz.output.name), ("cetz", "fig.svg"))
        penrose = parser.parse_args(["penrose", "d.trio.json", "-o", "d.svg", "--variation", "seed1"])
        self.assertEqual((penrose.engine, penrose.variation), ("penrose", "seed1"))

    # leaving out the engine subcommand is a usage error
    def test_requires_an_engine(self):
        with self.assertRaises(SystemExit):
            ri.build_parser().parse_args(["fig.typ", "-o", "fig.svg"])


# checks cetz input validation without running typst
class CetzTests(unittest.TestCase):
    # non typ input or unsupported output suffixes are refused
    def test_rejects_wrong_suffixes(self):
        with self.assertRaises(ValueError):
            ri.render_cetz(Path("fig.tex"), Path("fig.svg"))
        with self.assertRaises(ValueError):
            ri.render_cetz(Path("fig.typ"), Path("fig.mp4"))

    # a missing typst binary produces an error that names the typst cli
    def test_explains_missing_typst(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "fig.typ"
            source.write_text("#circle()")
            with patch.object(ri.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Typst CLI"):
                    ri.render_cetz(source, Path(tmp) / "fig.svg")


# checks penrose validation and the tool cache without running node
class PenroseTests(unittest.TestCase):
    # input must be a trio json and output must be svg
    def test_rejects_wrong_suffixes(self):
        with self.assertRaises(ValueError):
            ri.render_penrose(Path("d.json"), Path("d.svg"), variation=None, dump_steps=False, cache_root=Path("."))
        with self.assertRaises(ValueError):
            ri.render_penrose(Path("d.trio.json"), Path("d.png"), variation=None, dump_steps=False, cache_root=Path("."))

    # a missing npm produces an error that mentions node and npm
    def test_explains_missing_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(ri.shutil, "which", return_value=None):
                with self.assertRaisesRegex(RuntimeError, "Node.js and npm"):
                    ri.ensure_roger(Path(tmp))

    # the cache env var overrides the default location
    def test_tool_cache_honours_env(self):
        with patch.dict(ri.os.environ, {"VIDEO_USE_TOOL_CACHE": "/tmp/vu-cache"}):
            self.assertEqual(ri.default_tool_cache(), Path("/tmp/vu-cache").resolve())


if __name__ == "__main__":
    unittest.main()
