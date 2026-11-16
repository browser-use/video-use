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

FILES = 8


class InterruptTests(unittest.TestCase):
    def run_batch(self, workers, **patches):
        """Run main() over FILES fake videos until Ctrl-C; return the uploads started."""
        calls = []
        lock = threading.Lock()

        def fake_transcribe_one(video, **kwargs):
            with lock:
                calls.append(video.name)
            time.sleep(0.2)  # stands in for the Scribe upload
            return kwargs["edit_dir"] / "transcripts" / f"{video.stem}.json"

        with tempfile.TemporaryDirectory() as tmp:
            videos_dir = Path(tmp)
            for i in range(FILES):
                (videos_dir / f"clip{i}.mp4").touch()
            argv = ["transcribe_batch.py", str(videos_dir), "--workers", str(workers)]
            with patch.object(sys, "argv", argv), \
                 patch.object(transcribe_batch, "load_api_key", return_value="test-key"), \
                 patch.object(transcribe_batch, "transcribe_one", fake_transcribe_one), \
                 patch.multiple(transcribe_batch, **patches):
                with self.assertRaises(KeyboardInterrupt):
                    transcribe_batch.main()
            time.sleep(0.5)  # let any upload still running finish
        return calls

    def test_ctrl_c_while_waiting_stops_queued_uploads(self):
        """Ctrl-C must not leave the pool uploading (and billing) every queued file."""
        real_as_completed = transcribe_batch.as_completed

        def interrupted_as_completed(futures):
            # The user presses Ctrl-C once the first upload has finished; the
            # main thread is the one that receives KeyboardInterrupt.
            for _ in real_as_completed(futures):
                raise KeyboardInterrupt

        workers = 2
        calls = self.run_batch(workers, as_completed=interrupted_as_completed)
        # By the time the first upload finishes, each worker has started at most
        # two files (its first, and the next one it picked up). Nothing queued
        # may start after that; without the fix all of them are uploaded.
        self.assertLessEqual(len(calls), 2 * workers, f"uploads started: {calls}")

    def test_ctrl_c_while_submitting_stops_queued_uploads(self):
        """Ctrl-C that lands while files are still being queued must cancel them too."""
        real_pool = transcribe_batch.ThreadPoolExecutor

        class InterruptedPool(real_pool):
            submitted = 0

            def submit(self, *args, **kwargs):
                if InterruptedPool.submitted == FILES - 2:
                    raise KeyboardInterrupt
                InterruptedPool.submitted += 1
                return super().submit(*args, **kwargs)

        calls = self.run_batch(1, ThreadPoolExecutor=InterruptedPool)
        # One worker: the upload already running may finish (and possibly the
        # next one it picked up); without the fix every queued file is uploaded.
        self.assertLessEqual(len(calls), 2, f"uploads started: {calls}")


if __name__ == "__main__":
    unittest.main()
