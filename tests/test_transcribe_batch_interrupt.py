import importlib.util
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch


HELPERS = Path(__file__).parents[1] / "helpers"
sys.path.insert(0, str(HELPERS))  # transcribe_batch imports `transcribe` by name
SPEC = importlib.util.spec_from_file_location(
    "video_use_transcribe_batch", HELPERS / "transcribe_batch.py"
)
assert SPEC and SPEC.loader
transcribe_batch = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transcribe_batch)


class InterruptTests(unittest.TestCase):
    def test_ctrl_c_stops_queued_uploads(self):
        """Ctrl-C must not leave the pool uploading (and billing) every queued file."""
        calls = []
        lock = threading.Lock()

        def fake_transcribe_one(video, **kwargs):
            with lock:
                calls.append(video.name)
            time.sleep(0.2)  # stands in for the Scribe upload
            return kwargs["edit_dir"] / "transcripts" / f"{video.stem}.json"

        real_as_completed = transcribe_batch.as_completed
        started_at_interrupt = []

        def interrupted_as_completed(futures):
            # The user presses Ctrl-C once the first upload has finished; the
            # main thread is the one that receives KeyboardInterrupt.
            for _ in real_as_completed(futures):
                with lock:
                    started_at_interrupt.append(len(calls))
                raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as tmp:
            videos_dir = Path(tmp)
            for i in range(8):
                (videos_dir / f"clip{i}.mp4").touch()
            argv = ["transcribe_batch.py", str(videos_dir), "--workers", "2"]
            with patch.object(sys, "argv", argv), \
                 patch.object(transcribe_batch, "load_api_key", return_value="test-key"), \
                 patch.object(transcribe_batch, "transcribe_one", fake_transcribe_one), \
                 patch.object(transcribe_batch, "as_completed", interrupted_as_completed):
                with self.assertRaises(KeyboardInterrupt):
                    transcribe_batch.main()
            time.sleep(0.5)  # let any worker still running finish its current job

        # Uploads already running when Ctrl-C arrived may finish; no queued
        # file may start afterwards.
        self.assertEqual(started_at_interrupt, [len(calls)],
                         f"uploads started after Ctrl-C: {calls[started_at_interrupt[0]:]}")
        self.assertLess(len(calls), 8)


if __name__ == "__main__":
    unittest.main()
