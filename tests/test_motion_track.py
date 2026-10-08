"""Planar-tracking checks against independent, known image transformations."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
import tempfile
import unittest

import numpy as np

from helpers.motion_track import main, cv2, roi_quad, track_frames, track_video, validate_quad


# exercise planar tracking against known synthetic geometry and real decoding
@unittest.skipUnless(cv2 is not None, "Optional OpenCV tracking dependency is not installed")
class MotionTrackingTests(unittest.TestCase):
    width, height = 320, 240
    quad = [[58, 47], [247, 47], [247, 191], [58, 191]]

    # construct a reproducible textured surface for feature tracking
    def texture(self, seed):
        # Different test textures prevent a demonstration-specific feature layout.
        rng = np.random.default_rng(seed)
        image = np.zeros((self.height, self.width), dtype=np.uint8)
        patch = rng.integers(25, 230, size=(145, 190), dtype=np.uint8)
        patch = cv2.GaussianBlur(patch, (3, 3), .6)
        image[47:192, 58:248] = patch
        return image

    # generate known transformed frames and their independent expected geometry
    def sequence(self, seed, motion, count=24):
        original = self.texture(seed)
        images, transforms = [], []
        for frame in range(count):
            angle, scale, dx, dy = motion(frame)
            transform = np.eye(3)
            transform[:2] = cv2.getRotationMatrix2D((154, 119), angle, scale)
            transform[0, 2] += dx
            transform[1, 2] += dy
            images.append(cv2.warpPerspective(original, transform, (self.width, self.height)))
            transforms.append(transform)
        return images, transforms

    # verify translation and reverse direction
    def test_translation_and_reverse_direction(self):
        for seed, motion in [(3, lambda f: (0, 1, f*.75, f*.23)), (51, lambda f: (0, 1, -f*.67, f*.37))]:
            images, transforms = self.sequence(seed, motion)
            result = track_frames(images, [i/30 for i in range(len(images))], self.quad)
            self.assertTrue(all(frame["status"] != "lost" for frame in result))
            for frame, known in zip(result, transforms):
                expected = cv2.perspectiveTransform(np.array(self.quad, np.float32)[None], known)[0]
                self.assertLess(np.max(np.linalg.norm(np.array(frame["quad"])-expected, axis=1)), .85)

    # verify rotation scale and nonuniform times
    def test_rotation_scale_and_nonuniform_times(self):
        images, transforms = self.sequence(992, lambda f: (f*.32, 1+f*.003, f*.12, -f*.13))
        times = [2 + i/29.97 + (i%2)*.003 for i in range(len(images))]
        result = track_frames(images, times, self.quad)
        for frame, known, time in zip(result, transforms, times):
            self.assertEqual(frame["status"], "initialized" if frame["index"] == 0 else "tracked")
            expected = cv2.perspectiveTransform(np.array(self.quad, np.float32)[None], known)[0]
            self.assertLess(np.max(np.linalg.norm(np.array(frame["quad"])-expected, axis=1)), 1.4)
            self.assertAlmostEqual(frame["time"], time-2)
            self.assertEqual(frame["sourceTime"], time)
        json.dumps(result, allow_nan=False)

    # verify featureless region is explicitly lost
    def test_featureless_region_is_explicitly_lost(self):
        frames = [np.full((self.height, self.width), 100, np.uint8)] * 5
        result = track_frames(frames, [i/24 for i in range(5)], self.quad)
        self.assertTrue(all(frame["status"] == "lost" for frame in result))
        self.assertTrue(all(frame["homography"] is None and frame["quad"] is None for frame in result))
        self.assertEqual(result[0]["reason"], "insufficient_initial_features")

    # verify projective tilt recovers non affine corners
    def test_projective_tilt_recovers_non_affine_corners(self):
        image = self.texture(778)
        images, expected = [], []
        for index in range(18):
            transform = np.array([[1,index*.0006,index*.25],[index*.0003,1,index*.12],[index*.000013,-index*.000009,1]],dtype=float)
            images.append(cv2.warpPerspective(image,transform,(self.width,self.height)))
            expected.append(cv2.perspectiveTransform(np.array(self.quad,np.float32)[None],transform)[0])
        result = track_frames(images,[i/30 for i in range(18)],self.quad)
        for observed,known in zip(result,expected):
            self.assertNotEqual(observed['status'],'lost')
            self.assertLess(np.max(np.linalg.norm(np.array(observed['quad'])-known,axis=1)),1.5)

    # verify disappearance never hallucinates continuation
    def test_disappearance_never_hallucinates_continuation(self):
        images, _ = self.sequence(22, lambda f: (f*.2, 1, f*.2, 0), count=5)
        images += [np.zeros_like(images[0])] * 3
        images += [self.texture(22)] * 3
        result = track_frames(images, [i/30 for i in range(len(images))], self.quad)
        self.assertEqual(result[4]["status"], "tracked")
        self.assertEqual(result[5]["status"], "lost")
        self.assertTrue(all(frame["status"] == "lost" and frame["homography"] is None for frame in result[5:]))

    # verify invalid quad times and settings
    def test_invalid_quad_times_and_settings(self):
        for quad in ([[0,0]]*4, [[0,0],[20,20],[0,20],[20,0]], [[-1,0],[20,0],[20,20],[0,20]], [[0,0],[20,0],[20,float("nan")],[0,20]]):
            with self.subTest(quad=quad), self.assertRaises(ValueError):
                validate_quad(quad, self.width, self.height)
        image = self.texture(4)
        for times in ([], [0,0], [float("nan")], [1,0]):
            with self.subTest(times=times), self.assertRaises(ValueError):
                track_frames([image]*len(times), times, self.quad)
        for settings in ({"max_features": 0}, {"min_features": 3}, {"quality": float("inf")}, {"min_inlier_ratio": 2}):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                track_frames([image], [0], self.quad, **settings)
        with self.assertRaises(ValueError):
            track_frames([image], [0,.1], self.quad)
        with self.assertRaises(ValueError):
            track_frames([image,image], [0], self.quad)
        self.assertEqual(roi_quad("10,20,30,40"), [[10,20],[39,20],[39,59],[10,59]])

    # verify video decode frame count and timestamp metadata
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_video_decode_frame_count_and_timestamp_metadata(self):
        images, transforms = self.sequence(104, lambda f: (0, 1, f*.8, -f*.4), count=9)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source plane.avi"
            writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25, (self.width,self.height), isColor=False)
            self.assertTrue(writer.isOpened())
            for image in images:
                writer.write(image)
            writer.release()
            data = track_video(path, self.quad)
            self.assertEqual(data["source"]["frameCount"], 9)
            self.assertEqual(data["source"]["avgFrameRate"], "25/1")
            self.assertAlmostEqual(data["frames"][-1]["time"], 8/25)
            self.assertEqual(data["frames"][-1]["status"], "tracked")
            expected = cv2.perspectiveTransform(np.array(self.quad, np.float32)[None], transforms[-1])[0]
            self.assertLess(np.max(np.linalg.norm(np.array(data["frames"][-1]["quad"])-expected, axis=1)), .9)

    # verify 24fps exact frame boundaries and offset source duration
    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
    def test_24fps_exact_frame_boundaries_and_offset_source_duration(self):
        images, _ = self.sequence(849, lambda f: (0,1,f*.35,0), count=9)
        with tempfile.TemporaryDirectory() as folder:
            for offset in (0,5):
                with self.subTest(offset=offset):
                    path=Path(folder)/f"offset-{offset}.mp4"
                    command=['ffmpeg','-hide_banner','-v','error','-f','rawvideo','-pix_fmt','gray','-s',f'{self.width}x{self.height}','-r','24','-i','pipe:0','-vf',f'setpts=PTS+{offset}/TB','-fps_mode','passthrough','-c:v','libx264','-crf','16','-pix_fmt','yuv420p',str(path)]
                    encoded=subprocess.run(command,input=b''.join(image.tobytes() for image in images),capture_output=True)
                    self.assertEqual(encoded.returncode,0,encoded.stderr.decode())
                    data=track_video(path,self.quad)
                    self.assertEqual(data['source']['frameCount'],9)
                    self.assertEqual(data['source']['timestampPrecision'],'integer_pts')
                    self.assertEqual(data['source']['firstTimestamp'],offset)
                    self.assertEqual(data['source']['duration'],9/24)
                    self.assertEqual(data['source']['streamDuration'],9/24)
                    for index,frame in enumerate(data['frames']):
                        self.assertEqual(frame['time'],index/24)
                        self.assertEqual(frame['sourceTime'],offset+index/24)
                        # A held-frame consumer at the exact render boundary
                        # must select this frame rather than its predecessor.
                        selected=max(f['index'] for f in data['frames'] if f['time']<=index/24)
                        self.assertEqual(selected,index)


    @unittest.skipUnless(shutil.which("node"), "Node is required for the consumer handoff")
    def test_measured_track_is_consumed_at_exact_timestamps(self):
        images, _ = self.sequence(192, lambda f: (0, 1, f*.5, 0), count=6)
        frames = track_frames(images, [i/24 for i in range(6)], self.quad)
        track = {"schemaVersion": 1, "source": {"width": self.width, "height": self.height,
                 "frameCount": 6, "avgFrameRate": "24/1", "duration": 6/24},
                 "initialQuad": self.quad, "frames": frames}
        module = (Path(__file__).resolve().parents[1] / "skills/motion-design/runtime/tracking.mjs").as_uri()
        script = """
import assert from 'node:assert/strict';
import fs from 'node:fs';
const {createTrackSampler, applyHomography} = await import(process.argv[1]);
const track = JSON.parse(fs.readFileSync(0, 'utf8'));
const sample = createTrackSampler(track);
for (const index of [5, 0, 3, 1, 5]) {
  const result = sample(index/24);
  assert.equal(result.visible, true);
  assert.equal(result.frame.index, index);
  const point = applyHomography(result.frame.homography, {x:58, y:47});
  assert.ok(Math.abs(point.x - (58 + index*.5)) < 1);
}
assert.equal(sample(6/24).visible, false);
"""
        result = subprocess.run(["node", "--input-type=module", "-e", script, module],
                                input=json.dumps(track), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class OutputPreservationTests(unittest.TestCase):
    def test_existing_output_and_source_hardlink_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            source.write_bytes(b"original source")
            for kind in ("file", "hardlink", "symlink"):
                with self.subTest(kind=kind):
                    output = Path(folder) / f"{kind}.json"
                    if kind == "file":
                        output.write_bytes(b"earlier result")
                    elif kind == "hardlink":
                        output.hardlink_to(source)
                    else:
                        output.symlink_to(Path(folder) / "missing")
                    command = [sys.executable, "helpers/motion_track.py", str(source), "-o", str(output), "--roi", "0,0,20,20"]
                    result = subprocess.run(command, capture_output=True, text=True)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("output already exists", result.stderr)
                    self.assertEqual(source.read_bytes(), b"original source")
                    if kind == "file":
                        self.assertEqual(output.read_bytes(), b"earlier result")
                    if kind == "symlink":
                        self.assertTrue(output.is_symlink())

    def test_output_created_during_tracking_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.mp4"
            output = Path(folder) / "track.json"
            def complete_track(*args, **kwargs):
                output.write_text("concurrent result")
                return {"frames": []}
            argv = ["motion_track.py", str(source), "-o", str(output), "--roi", "0,0,20,20"]
            with patch.object(sys, "argv", argv), patch("helpers.motion_track.track_video", side_effect=complete_track):
                with self.assertRaises(SystemExit):
                    main()
            self.assertEqual(output.read_text(), "concurrent result")


if __name__ == "__main__":
    unittest.main()
