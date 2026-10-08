"""Check safe reuse and invalidation before connecting the cache to real media."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from helpers.render_cache import RenderCache


# keep each cache test isolated from user sources and installed media tools
class CacheTests(unittest.TestCase):
    # create a source and a deterministic stand in for a completed render
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source.bin"
        self.source.write_bytes(b"original")
        self.directory = self.root / "render-cache"
        self.cache = RenderCache(self.directory, runtime={"tools": "one"})
        self.output = self.root / "build/clip.mp4"
        self.render = Mock(side_effect=lambda path: path.write_bytes(b"rendered-clip"))

    # route the fixture through the same public cache entry point as the renderer
    def build(self, settings=None, destination=None, cache=None):
        return (cache or self.cache).get_or_render(settings or {"start": 0}, [self.source], destination or self.output, self.render)

    # reuse copies are independent of the cache so later tools cannot damage saved clips
    def test_reuse_and_output_independence(self):
        self.assertFalse(self.build())
        self.assertTrue(self.build(destination=self.root / "another.mp4"))
        self.assertEqual(self.render.call_count, 1)
        self.output.write_bytes(b"changed downstream")
        self.assertTrue(self.build())
        self.assertEqual(self.output.read_bytes(), b"rendered-clip")
        self.assertEqual(self.cache.summary(), {"reused_clips": 2, "rendered_clips": 1})

    # changing source bytes invalidates saved work even when size and mtime are restored
    def test_source_edit_with_preserved_mtime(self):
        self.build()
        before = self.source.stat()
        self.source.write_bytes(b"modified")
        os.utime(self.source, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assertFalse(self.build())
        self.assertEqual(self.render.call_count, 2)

    # changed ranges and tools cannot use an otherwise matching saved clip
    def test_settings_and_runtime_changes(self):
        self.build()
        self.assertFalse(self.build({"start": 1}))
        updated = RenderCache(self.directory, runtime={"tools": "two"})
        self.assertFalse(self.build(cache=updated))
        self.assertEqual(self.render.call_count, 3)

    # missing truncated or corrupted cache entries are rebuilt instead of accepted
    def test_corrupt_media_and_records(self):
        self.build()
        media = next(self.directory.glob("*.mp4"))
        media.write_bytes(b"corrupt-bytes")
        self.assertFalse(self.build())
        record = next(self.directory.glob("*.json"))
        for contents in ("{", "null", '{"version": 1, "bytes": "invalid"}'):
            with self.subTest(contents=contents):
                record.write_text(contents)
                self.assertFalse(self.build())
        record.unlink()
        self.assertFalse(self.build())
        self.assertTrue(self.build())

    # a renderer failure never leaves a reusable partial clip or destroys a prior output
    def test_failed_render_preserves_existing_output(self):
        self.output.parent.mkdir()
        self.output.write_bytes(b"prior output")

        # simulate an encoder failing after writing only part of its file
        def fail(path):
            path.write_bytes(b"partial")
            raise RuntimeError("encode failed")

        self.render.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, "encode failed"):
            self.build()
        self.assertEqual(self.output.read_bytes(), b"prior output")
        self.assertEqual(list(self.directory.iterdir()), [])

    # a changing source aborts rather than blessing an uncertain render as reusable
    def test_input_change_during_render(self):
        # mutate a source after the cache has recorded its initial state
        def mutate(path):
            path.write_bytes(b"rendered")
            self.source.write_bytes(b"new source")

        self.render.side_effect = mutate
        with self.assertRaisesRegex(RuntimeError, "Source changed"):
            self.build()
        self.assertFalse(self.output.exists())
        self.assertEqual(list(self.directory.iterdir()), [])

    # missing outputs and nonfinite settings fail before a cache entry can be published
    def test_empty_render_and_invalid_settings(self):
        self.render.side_effect = lambda path: path.touch()
        with self.assertRaisesRegex(ValueError, "complete clip"):
            self.build()
        with self.assertRaises(ValueError):
            self.build({"start": float("nan")})
        self.assertEqual(list(self.directory.iterdir()), [])

    # source aliases must not be treated as disposable build paths
    def test_output_cannot_alias_any_source(self):
        with self.assertRaisesRegex(ValueError, "overwrite a source"):
            self.build(destination=self.source)
        alias = self.root / "alias.mp4"
        os.link(self.source, alias)
        with self.assertRaisesRegex(ValueError, "overwrite a source"):
            self.build(destination=alias)
        other = self.root / "other.bin"
        other.write_bytes(b"other source")
        protected = RenderCache(self.directory, runtime={}, protected_inputs=[other])
        with self.assertRaisesRegex(ValueError, "overwrite a source"):
            self.build(destination=other, cache=protected)
        self.assertEqual(self.source.read_bytes(), b"original")
        self.render.assert_not_called()

    # links cannot redirect the generated cache or its published entries
    def test_rejects_cache_and_output_links(self):
        link = self.root / "cache-link"
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            RenderCache(link, runtime={})
        self.build()
        media = next(self.directory.glob("*.mp4"))
        media.unlink()
        media.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "symbolic link"):
            self.build()
        output = self.root / "output-link.mp4"
        output.symlink_to(self.source)
        with self.assertRaisesRegex(ValueError, "cannot be a link"):
            self.build(destination=output)
        self.assertEqual(self.source.read_bytes(), b"original")

    # user source files must never occupy the directory reserved for generated cache entries
    def test_inputs_cannot_live_inside_cache(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            RenderCache(self.root, runtime={}, protected_inputs=[self.source])

    # readers detect a data and record mismatch from concurrent publication
    def test_mismatched_publication_is_a_miss(self):
        self.build()
        record = next(self.directory.glob("*.json"))
        value = json.loads(record.read_text())
        value["sha256"] = "0" * 64
        record.write_text(json.dumps(value))
        self.assertFalse(self.build())
        self.assertEqual(self.output.read_bytes(), b"rendered-clip")


# Confirm that the legacy renderer includes every clip setting in its reuse decisions
class LegacyCacheTests(unittest.TestCase):
    # exercise two unchanged cuts then change only one range and finally the frame rate
    def test_only_changed_ranges_are_rendered(self):
        import helpers.render as render
        import render_cache

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source.mp4").write_bytes(b"source fixture")
            edl = {"sources": {"a": "source.mp4"}, "ranges": [
                {"source": "a", "start": 0, "end": 1}, {"source": "a", "start": 1, "end": 2}]}
            # make every extraction produce distinct bytes tied to its source window
            def extract(source, start, duration, grade, output, **options):
                output.write_bytes(json.dumps([start, duration, grade, options], sort_keys=True).encode())

            with patch.object(render_cache, "runtime_signature", return_value={}), patch.object(render, "extract_segment", side_effect=extract) as encoder:
                first = render.extract_all_segments(edl, root, False, fps="30", reuse=True)
                contents = [p.read_bytes() for p in first]
                second = render.extract_all_segments(edl, root, False, fps="30", reuse=True)
                self.assertEqual([p.read_bytes() for p in second], contents)
                self.assertEqual(encoder.call_count, 2)
                edl["ranges"][1]["end"] = 2.5
                render.extract_all_segments(edl, root, False, fps="30", reuse=True)
                self.assertEqual(encoder.call_count, 3)
                render.extract_all_segments(edl, root, False, fps="24", reuse=True)
                self.assertEqual(encoder.call_count, 5)

    # an undeclared filter dependency must force extraction even on a repeated command
    def test_raw_filters_bypass_cache(self):
        import helpers.render as render
        import render_cache

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "source.mp4").write_bytes(b"source fixture")
            edl = {"sources": {"a": "source.mp4"}, "ranges": [{"source": "a", "start": 0, "end": 1}], "grade": "lut3d=look.cube"}
            with patch.object(render_cache, "runtime_signature", return_value={}), patch.object(render, "extract_segment", side_effect=lambda *args, **kwargs: args[4].write_bytes(b"clip")) as encoder:
                render.extract_all_segments(edl, root, False, fps="30", reuse=True)
                render.extract_all_segments(edl, root, False, fps="30", reuse=True)
                self.assertEqual(encoder.call_count, 2)
                self.assertEqual(list((root / "render-cache").glob("*.mp4")), [])


if __name__ == "__main__":
    unittest.main()
