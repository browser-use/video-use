"""Original regression fixtures: cues must not cross their edit's boundary."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('render_bounds', Path(__file__).parents[1] / 'helpers' / 'render.py')
render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(render)


class CaptionBoundaryTests(unittest.TestCase):
    def test_zero_length_word_does_not_invent_four_hundred_ms(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'transcripts').mkdir()
            (root / 'transcripts' / 'take.json').write_text(json.dumps({'words': [
                {'type': 'word', 'text': 'ghost', 'start': 0.95, 'end': 0.95}]}))
            out = root / 'captions.srt'
            render.build_master_srt({'sources': {'take': 'unused.mp4'}, 'ranges': [
                {'source': 'take', 'start': 0.0, 'end': 1.0}]}, root, out)
            self.assertEqual(out.read_text(), '')

    def test_invalid_word_intervals_are_ignored(self):
        words = [{'type': 'word', 'text': 'bad', 'start': a, 'end': b}
                 for a, b in [(0.5, 0.4), (0.4, 0.4), (float('nan'), 0.8),
                              (0.2, float('inf')), ('0.2', 0.5), (True, 1.2)]]
        good = {'type': 'word', 'text': 'valid', 'start': 0.2, 'end': 0.5}
        self.assertEqual(render._words_in_range({'words': words + [good]}, 0, 1), [good])

    def test_submillisecond_cue_does_not_serialize_as_empty_interval(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'transcripts').mkdir()
            (root / 'transcripts' / 'take.json').write_text(json.dumps({'words': [
                {'type': 'word', 'text': 'clipped', 'start': 0.1, 'end': 0.1001}]}))
            out = root / 'captions.srt'
            render.build_master_srt({'sources': {'take': 'unused.mp4'}, 'ranges': [
                {'source': 'take', 'start': 0, 'end': 1}]}, root, out)
            self.assertEqual(out.read_text(), '')

    def test_partial_word_stays_within_its_clip(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / 'transcripts').mkdir()
            (root / 'transcripts' / 'take.json').write_text(json.dumps({'words': [
                {'type': 'word', 'text': 'partial', 'start': 0.8, 'end': 1.4}]}))
            out = root / 'captions.srt'
            render.build_master_srt({'sources': {'take': 'unused.mp4'}, 'ranges': [
                {'source': 'take', 'start': 0, 'end': 1}]}, root, out)
            self.assertIn('00:00:00,800 --> 00:00:01,000', out.read_text())


if __name__ == '__main__':
    unittest.main()
