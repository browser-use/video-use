import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import import_sources


class FakeResponse:
    def __init__(
        self, *, status_code=200, payload=None, body=b"video", url=None, headers=None
    ):
        self.status_code = status_code
        self.payload = payload
        self.body = body
        self.url = url
        self.headers = headers or {}

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        del chunk_size
        if isinstance(self.body, Exception):
            raise self.body
        yield self.body

    def close(self):
        pass


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class ImportSourcesTest(unittest.TestCase):
    def test_parse_x_post_id_accepts_supported_hosts(self):
        tweet_id = "1893456789012345678"

        for host in ("x.com", "www.x.com", "twitter.com", "mobile.twitter.com"):
            with self.subTest(host=host):
                self.assertEqual(
                    tweet_id,
                    import_sources.parse_x_post_id(
                        f"https://{host}/xquik/status/{tweet_id}?s=20"
                    ),
                )

    def test_parse_x_post_id_rejects_lookalike_hosts_and_short_ids(self):
        with self.assertRaisesRegex(import_sources.SourceImportError, "Expected"):
            import_sources.parse_x_post_id(
                "https://x.com.attacker.example/user/status/1893456789012345678"
            )
        with self.assertRaisesRegex(import_sources.SourceImportError, "15 to 20"):
            import_sources.parse_x_post_id("https://x.com/user/status/123")

    def test_malformed_bracketed_host_is_a_source_error(self):
        with self.assertRaisesRegex(import_sources.SourceImportError, "valid HTTPS"):
            import_sources._validate_generic_url("https://[::1")

    def test_selects_highest_bitrate_mp4_for_each_video(self):
        payload = {
            "tweet": {
                "media": [
                    {"type": "photo", "mediaUrl": "https://pbs.twimg.com/a.jpg"},
                    {
                        "type": "video",
                        "videoVariants": [
                            {
                                "contentType": "application/x-mpegURL",
                                "url": "https://video.twimg.com/a.m3u8",
                            },
                            {
                                "bitrate": 832000,
                                "contentType": "video/mp4",
                                "url": "https://video.twimg.com/low.mp4",
                            },
                            {
                                "bitrate": 2176000,
                                "contentType": "video/mp4",
                                "url": "https://video.twimg.com/high.mp4",
                            },
                        ],
                    },
                ]
            }
        }

        self.assertEqual(
            [import_sources.VideoVariant(2, "https://video.twimg.com/high.mp4")],
            import_sources.select_mp4_variants(payload),
        )

    def test_select_variants_rejects_untrusted_media_host(self):
        payload = {
            "tweet": {
                "media": [
                    {
                        "type": "video",
                        "videoVariants": [
                            {
                                "bitrate": 1,
                                "contentType": "video/mp4",
                                "url": "https://video.twimg.com.attacker.example/video.mp4",
                            }
                        ],
                    }
                ]
            }
        }

        with self.assertRaisesRegex(import_sources.SourceImportError, "no direct MP4"):
            import_sources.select_mp4_variants(payload)

    def test_import_x_post_uses_xquik_and_publishes_verified_file(self):
        tweet_id = "1893456789012345678"
        payload = {
            "tweet": {
                "media": [
                    {
                        "type": "video",
                        "videoVariants": [
                            {
                                "bitrate": 2176000,
                                "contentType": "video/mp4",
                                "url": "https://video.twimg.com/video.mp4",
                            }
                        ],
                    }
                ]
            }
        }
        session = FakeSession(
            [
                FakeResponse(payload=payload),
                FakeResponse(url="https://video.twimg.com/video.mp4"),
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = import_sources.import_x_post(
                f"https://x.com/xquik/status/{tweet_id}",
                Path(temp_dir),
                "test-key",
                session,
                verifier=lambda path: self.assertTrue(path.is_file()),
            )

            self.assertEqual([Path(temp_dir) / f"x-{tweet_id}-1.mp4"], outputs)
            self.assertEqual(b"video", outputs[0].read_bytes())
            self.assertEqual(
                {"x-api-key": "test-key"},
                session.calls[0][1]["headers"],
            )
            self.assertEqual((10, 30), session.calls[0][1]["timeout"])
            self.assertNotIn("headers", session.calls[1][1])

    def test_download_rejects_redirect_and_removes_partial_file(self):
        session = FakeSession(
            [FakeResponse(url="https://attacker.example/video.mp4", body=b"video")]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            destination = Path(temp_dir) / "video.mp4"
            with self.assertRaisesRegex(
                import_sources.SourceImportError, "unexpected host"
            ):
                import_sources.download_video(
                    "https://video.twimg.com/video.mp4",
                    destination,
                    session,
                    max_bytes=100,
                )
            self.assertFalse(destination.exists())
            self.assertEqual([], list(Path(temp_dir).glob("*.part")))
            self.assertFalse(session.calls[0][1]["allow_redirects"])

    def test_download_enforces_streamed_size_limit(self):
        session = FakeSession(
            [FakeResponse(url="https://video.twimg.com/video.mp4", body=b"12345")]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(import_sources.SourceImportError, "size limit"):
                import_sources.download_video(
                    "https://video.twimg.com/video.mp4",
                    Path(temp_dir) / "video.mp4",
                    session,
                    max_bytes=4,
                )
            self.assertEqual([], list(Path(temp_dir).iterdir()))

    def test_download_ignores_invalid_content_length_and_counts_bytes(self):
        session = FakeSession(
            [
                FakeResponse(
                    url="https://video.twimg.com/video.mp4",
                    body=b"video",
                    headers={"content-length": "unknown"},
                )
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = import_sources.download_video(
                "https://video.twimg.com/video.mp4",
                Path(temp_dir) / "video.mp4",
                session,
                max_bytes=10,
                verifier=lambda path: self.assertTrue(path.is_file()),
            )
            self.assertEqual(b"video", output.read_bytes())

    def test_stream_failure_is_wrapped_and_removes_partial_file(self):
        session = FakeSession(
            [
                FakeResponse(
                    url="https://video.twimg.com/video.mp4",
                    body=import_sources.requests.ConnectionError("stream failed"),
                )
            ]
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(
                import_sources.SourceImportError, "download failed"
            ):
                import_sources.download_video(
                    "https://video.twimg.com/video.mp4",
                    Path(temp_dir) / "video.mp4",
                    session,
                    max_bytes=10,
                )
            self.assertEqual([], list(Path(temp_dir).iterdir()))

    def test_ytdlp_is_bounded_and_validates_reported_output(self):
        captured = []
        runner_kwargs = []

        def fake_runner(command, **kwargs):
            captured.append(command)
            runner_kwargs.append(kwargs)
            output_name = command[command.index("--output") + 1].replace(
                "%(ext)s", "mp4"
            )
            output = Path(command[command.index("--paths") + 1]) / output_name
            output.write_bytes(b"video")
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{output}\n", stderr=""
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            output = import_sources.import_with_ytdlp(
                "https://example.com/video",
                Path(temp_dir),
                runner=fake_runner,
                executable="yt-dlp",
                verifier=lambda path: self.assertTrue(path.is_file()),
            )
            self.assertRegex(output.name, r"^source-[0-9a-f]{16}\.mp4$")
            self.assertTrue(output.is_file())

        self.assertIn("--no-playlist", captured[0])
        self.assertIn("--restrict-filenames", captured[0])
        self.assertEqual(
            str(import_sources.DEFAULT_MAX_BYTES),
            captured[0][captured[0].index("--max-filesize") + 1],
        )
        self.assertIn("--force-overwrites", captured[0])
        self.assertIn("--remux-video", captured[0])
        self.assertEqual(
            "best[ext=mp4]/best", captured[0][captured[0].index("--format") + 1]
        )
        self.assertTrue(callable(runner_kwargs[0]["preexec_fn"]))

    def test_ytdlp_reuses_verified_destination_before_running(self):
        url = "https://example.com/video"
        verified = []

        with tempfile.TemporaryDirectory() as temp_dir:
            downloads = Path(temp_dir)
            destination = import_sources._generic_destination(url, downloads)
            destination.write_bytes(b"video")

            output = import_sources.import_with_ytdlp(
                url,
                downloads,
                executable="yt-dlp",
                runner=lambda *args, **kwargs: self.fail(
                    f"unexpected download: {args}, {kwargs}"
                ),
                verifier=lambda path: verified.append(path),
            )

            self.assertEqual(destination, output)
            self.assertEqual([destination], verified)

    def test_ytdlp_child_cannot_write_past_size_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            executable = root / "fake-yt-dlp"
            executable.write_text(
                """#!/usr/bin/env python3
import sys
from pathlib import Path

args = sys.argv[1:]
root = Path(args[args.index("--paths") + 1])
name = args[args.index("--output") + 1].replace("%(ext)s", "mp4")
output = root / name
output.write_bytes(b"12345")
print(output)
""",
                encoding="utf-8",
            )
            executable.chmod(0o755)
            downloads = root / "downloads"

            with self.assertRaisesRegex(import_sources.SourceImportError, "size limit"):
                import_sources.import_with_ytdlp(
                    "https://example.com/chunked-video",
                    downloads,
                    max_bytes=4,
                    executable=str(executable),
                    verifier=lambda path: self.fail(f"unexpected probe: {path}"),
                )

            self.assertEqual([], list(downloads.iterdir()))

    def test_ytdlp_fails_closed_without_posix_file_limits(self):
        original_resource = import_sources._resource
        import_sources._resource = None
        try:
            self.assertEqual(
                "1893456789012345678",
                import_sources.parse_x_post_id(
                    "https://x.com/xquik/status/1893456789012345678"
                ),
            )
            with (
                tempfile.TemporaryDirectory() as temp_dir,
                self.assertRaisesRegex(
                    import_sources.SourceImportError, "hard child file-size limit"
                ),
            ):
                import_sources.import_with_ytdlp(
                    "https://example.com/video",
                    Path(temp_dir),
                    executable="yt-dlp",
                    runner=lambda *args, **kwargs: self.fail(
                        f"unexpected runner call: {args}, {kwargs}"
                    ),
                )
        finally:
            import_sources._resource = original_resource

    def test_ytdlp_rejects_oversize_output_without_publishing_it(self):
        def fake_runner(command, **kwargs):
            del kwargs
            output_name = command[command.index("--output") + 1].replace(
                "%(ext)s", "mp4"
            )
            output = Path(command[command.index("--paths") + 1]) / output_name
            output.write_bytes(b"12345")
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{output}\n", stderr=""
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            downloads = Path(temp_dir) / "downloads"
            with self.assertRaisesRegex(import_sources.SourceImportError, "size limit"):
                import_sources.import_with_ytdlp(
                    "https://example.com/video",
                    downloads,
                    max_bytes=4,
                    runner=fake_runner,
                    executable="yt-dlp",
                    verifier=lambda path: self.fail(f"unexpected probe: {path}"),
                )
            self.assertEqual([], list(downloads.iterdir()))

    def test_ytdlp_probe_failure_leaves_downloads_empty(self):
        def fake_runner(command, **kwargs):
            del kwargs
            output_name = command[command.index("--output") + 1].replace(
                "%(ext)s", "mp4"
            )
            output = Path(command[command.index("--paths") + 1]) / output_name
            output.write_bytes(b"audio")
            return subprocess.CompletedProcess(
                command, 0, stdout=f"{output}\n", stderr=""
            )

        def reject_video(path):
            raise import_sources.SourceImportError(f"invalid video: {path.name}")

        with tempfile.TemporaryDirectory() as temp_dir:
            downloads = Path(temp_dir) / "downloads"
            with self.assertRaisesRegex(
                import_sources.SourceImportError, "invalid video"
            ):
                import_sources.import_with_ytdlp(
                    "https://example.com/video",
                    downloads,
                    runner=fake_runner,
                    executable="yt-dlp",
                    verifier=reject_video,
                )
            self.assertEqual([], list(downloads.iterdir()))

    def test_ytdlp_rejects_x_host_lookalikes_before_running(self):
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            self.assertRaisesRegex(
                import_sources.SourceImportError, "canonical X post"
            ),
        ):
            import_sources.import_with_ytdlp(
                "https://x.com.attacker.example/video",
                Path(temp_dir),
                executable="yt-dlp",
                runner=lambda *args, **kwargs: self.fail(
                    f"unexpected runner call: {args}, {kwargs}"
                ),
            )

    def test_verify_video_requires_a_video_stream(self):
        def failed_probe(command, **kwargs):
            del command, kwargs
            return subprocess.CompletedProcess([], 0, stdout="audio\n", stderr="")

        with self.assertRaisesRegex(import_sources.SourceImportError, "video stream"):
            import_sources.verify_video(Path("fixture.mp4"), runner=failed_probe)


if __name__ == "__main__":
    unittest.main()
