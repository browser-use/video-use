"""Import online videos into an edit session.

X post URLs use Xquik's tweet lookup API to select the highest-bitrate MP4.
Other HTTPS URLs use yt-dlp on platforms with POSIX file-size limits. Downloads
are verified with ffprobe before they become visible in ``<edit_dir>/downloads``.

Usage:
    python helpers/import_sources.py <url> [<url> ...]
    python helpers/import_sources.py <url> --edit-dir /path/to/edit
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    import resource as _resource
except ImportError:
    _resource = None

XQUIK_TWEET_URL = "https://xquik.com/api/v1/x/tweets/{tweet_id}"
X_POST_HOSTS = {
    "mobile.twitter.com",
    "mobile.x.com",
    "twitter.com",
    "www.twitter.com",
    "www.x.com",
    "x.com",
}
X_MEDIA_HOSTS = {"video.twimg.com"}
X_PROTECTED_DOMAINS = {"twitter.com", "twimg.com", "x.com"}
DEFAULT_MAX_BYTES = 500 * 1024 * 1024


class SourceImportError(RuntimeError):
    """A source could not be imported safely."""


@dataclass(frozen=True)
class VideoVariant:
    media_index: int
    url: str


def _hostname(url: str) -> str:
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as error:
        raise SourceImportError("Use a valid HTTPS URL.") from error
    if parsed.scheme != "https" or not hostname:
        raise SourceImportError("Use a complete HTTPS URL.")
    if parsed.username or parsed.password:
        raise SourceImportError("URLs with embedded credentials are not supported.")
    return hostname.rstrip(".").lower()


def _host_matches(hostname: str, allowed_hosts: set[str]) -> bool:
    return any(
        hostname == allowed or hostname.endswith(f".{allowed}")
        for allowed in allowed_hosts
    )


def is_x_post_url(url: str) -> bool:
    try:
        return _hostname(url) in X_POST_HOSTS
    except SourceImportError:
        return False


def parse_x_post_id(url: str) -> str:
    if _hostname(url) not in X_POST_HOSTS:
        raise SourceImportError("Expected an x.com or twitter.com post URL.")

    parts = [part for part in urlparse(url).path.split("/") if part]
    try:
        status_index = parts.index("status")
        tweet_id = parts[status_index + 1]
    except (ValueError, IndexError) as error:
        raise SourceImportError("X post URL is missing its status ID.") from error

    if not tweet_id.isdigit() or not 15 <= len(tweet_id) <= 20:
        raise SourceImportError("X post status ID must contain 15 to 20 digits.")
    return tweet_id


def select_mp4_variants(payload: object) -> list[VideoVariant]:
    if not isinstance(payload, dict) or not isinstance(payload.get("tweet"), dict):
        raise SourceImportError("Xquik returned an invalid tweet response.")

    media = payload["tweet"].get("media")
    if not isinstance(media, list):
        raise SourceImportError("The X post has no downloadable video.")

    selected: list[VideoVariant] = []
    for media_index, item in enumerate(media, start=1):
        if not isinstance(item, dict) or item.get("type") not in {
            "animated_gif",
            "video",
        }:
            continue

        raw_variants = item.get("videoVariants", item.get("video_variants"))
        if not isinstance(raw_variants, list):
            continue

        candidates: list[tuple[int, str]] = []
        for variant in raw_variants:
            if not isinstance(variant, dict):
                continue
            content_type = variant.get("contentType", variant.get("content_type"))
            url = variant.get("url")
            if content_type != "video/mp4" or not isinstance(url, str):
                continue
            if not _host_matches(_hostname(url), X_MEDIA_HOSTS):
                continue
            bitrate = variant.get("bitrate")
            candidates.append((bitrate if isinstance(bitrate, int) else -1, url))

        if candidates:
            selected.append(
                VideoVariant(media_index=media_index, url=max(candidates)[1])
            )

    if not selected:
        raise SourceImportError("The X post has no direct MP4 rendition.")
    return selected


def fetch_x_post(
    tweet_id: str,
    api_key: str,
    session: requests.Session,
) -> object:
    try:
        response = session.get(
            XQUIK_TWEET_URL.format(tweet_id=tweet_id),
            headers={"x-api-key": api_key},
            timeout=(10, 30),
        )
    except requests.RequestException as error:
        raise SourceImportError("Xquik request failed. Retry the import.") from error

    try:
        if response.status_code != 200:
            raise SourceImportError(
                f"Xquik returned HTTP {response.status_code}. "
                "Check the API key and credits."
            )
        try:
            return response.json()
        except requests.JSONDecodeError as error:
            raise SourceImportError("Xquik returned invalid JSON.") from error
    finally:
        response.close()


def verify_video(
    path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> None:
    result = runner(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or result.stdout.strip() != "video":
        raise SourceImportError(
            "Downloaded file does not contain a readable video stream."
        )


def download_video(
    url: str,
    destination: Path,
    session: requests.Session,
    max_bytes: int,
    force: bool = False,
    verifier: Callable[[Path], None] = verify_video,
) -> Path:
    if destination.exists() and not force:
        if destination.stat().st_size > max_bytes:
            raise SourceImportError("X video exceeds the configured size limit.")
        verifier(destination)
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        try:
            response = session.get(
                url,
                allow_redirects=False,
                stream=True,
                timeout=(10, 120),
            )
        except requests.RequestException as error:
            raise SourceImportError(
                "X media download failed. Retry the import."
            ) from error

        try:
            if response.status_code != 200:
                raise SourceImportError(
                    f"X media download returned HTTP {response.status_code}."
                )
            final_url = str(getattr(response, "url", url))
            if not _host_matches(_hostname(final_url), X_MEDIA_HOSTS):
                raise SourceImportError("X media redirected to an unexpected host.")

            content_length = response.headers.get("content-length")
            try:
                declared_bytes = int(content_length) if content_length else None
            except ValueError:
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > max_bytes:
                raise SourceImportError("X video exceeds the configured size limit.")

            with tempfile.NamedTemporaryFile(
                dir=destination.parent,
                prefix=f".{destination.name}.",
                suffix=".part",
                delete=False,
            ) as output:
                temp_path = Path(output.name)
                downloaded = 0
                try:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if not chunk:
                            continue
                        downloaded += len(chunk)
                        if downloaded > max_bytes:
                            raise SourceImportError(
                                "X video exceeds the configured size limit."
                            )
                        output.write(chunk)
                except requests.RequestException as error:
                    raise SourceImportError(
                        "X media download failed. Retry the import."
                    ) from error
        finally:
            response.close()

        verifier(temp_path)
        os.replace(temp_path, destination)
        temp_path = None
        return destination
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def import_x_post(
    url: str,
    downloads_dir: Path,
    api_key: str,
    session: requests.Session,
    max_bytes: int = DEFAULT_MAX_BYTES,
    force: bool = False,
    verifier: Callable[[Path], None] = verify_video,
) -> list[Path]:
    tweet_id = parse_x_post_id(url)
    payload = fetch_x_post(tweet_id, api_key, session)
    variants = select_mp4_variants(payload)
    return [
        download_video(
            variant.url,
            downloads_dir / f"x-{tweet_id}-{variant.media_index}.mp4",
            session,
            max_bytes,
            force=force,
            verifier=verifier,
        )
        for variant in variants
    ]


def _validate_generic_url(url: str) -> None:
    host = _hostname(url)
    if any(
        host == domain
        or host.startswith(f"{domain}.")
        or f".{domain}." in host
        or host.endswith(f".{domain}")
        for domain in X_PROTECTED_DOMAINS
    ):
        raise SourceImportError("Use a canonical X post URL for X media.")
    if host == "localhost":
        raise SourceImportError("Local network URLs are not supported.")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise SourceImportError("Local network URLs are not supported.")


def _generic_destination(url: str, downloads_dir: Path) -> Path:
    source_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return downloads_dir / f"source-{source_id}.mp4"


def _limit_child_file_size(max_bytes: int) -> Callable[[], None]:
    if _resource is None:
        raise SourceImportError(
            "Generic URL imports are unavailable because this platform cannot "
            "enforce a hard child file-size limit."
        )

    def apply_limit() -> None:
        soft, hard = _resource.getrlimit(_resource.RLIMIT_FSIZE)
        finite_limits = [max_bytes]
        if soft != _resource.RLIM_INFINITY:
            finite_limits.append(soft)
        if hard != _resource.RLIM_INFINITY:
            finite_limits.append(hard)
        _resource.setrlimit(_resource.RLIMIT_FSIZE, (min(finite_limits), hard))

    return apply_limit


def _staged_file_reached_limit(temp_root: Path, max_bytes: int) -> bool:
    return any(
        path.is_file() and path.stat().st_size >= max_bytes
        for path in temp_root.rglob("*")
    )


def import_with_ytdlp(
    url: str,
    downloads_dir: Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
    force: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    executable: str | None = None,
    verifier: Callable[[Path], None] = verify_video,
) -> Path:
    _validate_generic_url(url)
    destination = _generic_destination(url, downloads_dir)
    if destination.exists() and not force:
        if not destination.is_file():
            raise SourceImportError("Import destination is not a regular file.")
        if destination.stat().st_size > max_bytes:
            raise SourceImportError("Video exceeds the configured size limit.")
        verifier(destination)
        return destination

    executable = executable or shutil.which("yt-dlp")
    if not executable:
        raise SourceImportError("yt-dlp is required for non-X URLs. Install it first.")

    downloads_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=downloads_dir.parent,
        prefix=f".{downloads_dir.name}-import-",
    ) as temp_name:
        temp_root = Path(temp_name).resolve()
        command = [
            executable,
            "--no-playlist",
            "--restrict-filenames",
            "--merge-output-format",
            "mp4",
            "--remux-video",
            "mp4",
            "--format",
            "best[ext=mp4]/best",
            "--max-filesize",
            str(max_bytes),
            "--paths",
            str(temp_root),
            "--output",
            f"{destination.stem}.%(ext)s",
            "--print",
            "after_move:filepath",
            "--force-overwrites",
            url,
        ]
        result = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            preexec_fn=_limit_child_file_size(max_bytes),
        )
        if result.returncode != 0:
            if _staged_file_reached_limit(temp_root, max_bytes):
                raise SourceImportError("Video exceeds the configured size limit.")
            raise SourceImportError("yt-dlp could not import the source URL.")

        output_lines = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        if not output_lines:
            raise SourceImportError("yt-dlp did not report an output file.")
        output_path = Path(output_lines[-1]).resolve()
        if (
            output_path.parent != temp_root
            or output_path.name != destination.name
            or not output_path.is_file()
        ):
            raise SourceImportError("yt-dlp reported an unexpected output path.")
        if output_path.stat().st_size > max_bytes:
            raise SourceImportError("Video exceeds the configured size limit.")
        verifier(output_path)

        os.replace(output_path, destination)
        return destination


def main() -> None:
    parser = argparse.ArgumentParser(description="Import online videos for video-use")
    parser.add_argument("sources", nargs="+", help="X post or supported video URL")
    parser.add_argument(
        "--edit-dir",
        type=Path,
        default=Path("edit"),
        help="Edit output directory (default: ./edit)",
    )
    parser.add_argument(
        "--max-size-mb",
        type=int,
        default=500,
        help="Maximum X video size in MiB (default: 500)",
    )
    parser.add_argument("--force", action="store_true", help="Replace existing imports")
    args = parser.parse_args()

    if args.max_size_mb < 1:
        parser.error("--max-size-mb must be at least 1")

    downloads_dir = args.edit_dir.resolve() / "downloads"
    x_sources = [source for source in args.sources if is_x_post_url(source)]
    api_key = os.environ.get("X_TWITTER_SCRAPER_API_KEY", "")
    if x_sources and not api_key:
        sys.exit(
            "X_TWITTER_SCRAPER_API_KEY is required for X post imports. "
            "Create a key at https://xquik.com."
        )

    try:
        with requests.Session() as session:
            for source in args.sources:
                if is_x_post_url(source):
                    imported = import_x_post(
                        source,
                        downloads_dir,
                        api_key,
                        session,
                        max_bytes=args.max_size_mb * 1024 * 1024,
                        force=args.force,
                    )
                else:
                    imported = [
                        import_with_ytdlp(
                            source,
                            downloads_dir,
                            max_bytes=args.max_size_mb * 1024 * 1024,
                            force=args.force,
                        )
                    ]
                for path in imported:
                    print(path)
    except SourceImportError as error:
        sys.exit(f"source import failed: {error}")


if __name__ == "__main__":
    main()
