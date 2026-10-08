"""Create smaller review movies while verifying their timing against the originals."""

import argparse
from fractions import Fraction
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

try:
    from .edit_io import sha256
except ImportError:
    from edit_io import sha256


# Encode finite records consistently for stable copy identities
def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


# Capture source and cache file state without loading video bytes into memory
def file_state(path):
    info = Path(path).stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"Expected a regular file: {path}")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


# Bound media processes while keeping long logs on disk
def run(args, folder, timeout, stdout=None):
    log = Path(folder) / "command.log"
    with log.open("wb") as stderr:
        try:
            result = subprocess.run([str(arg) for arg in args], stdin=subprocess.DEVNULL,
                                    stdout=stdout if stdout is not None else subprocess.DEVNULL,
                                    stderr=stderr, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"{args[0]} exceeded {timeout:g}s; increase --timeout for longer footage") from exc
    if result.returncode:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 4000))
            detail = stream.read().decode(errors="replace")
        raise ValueError(f"{args[0]} failed: {detail}")


# Read stream facts with the same bounded process policy as encoding
def probe(path, folder, timeout):
    report = Path(folder) / "probe.json"
    with report.open("wb") as stream:
        run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", path], folder, timeout, stream)
    return json.loads(report.read_bytes())


# Keep tool paths builds and helper code in the review copy identity
def runtime_identity():
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if path is None:
            raise ValueError(f"Install {name} before making a review copy")
        result = subprocess.run([path, "-version"], capture_output=True, check=True, timeout=20)
        tools[name] = {"path": str(Path(path).resolve()), "version": result.stdout.decode(errors="replace")}
    return {"tools": tools, "code": sha256(__file__)}


# Accept finite timeline facts and return useful errors for incomplete media metadata
def numeric(value, name):
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f"Missing or invalid {name}") from exc
    if isinstance(value, bool) or not math.isfinite(result):
        raise ValueError(f"Missing or invalid {name}")
    return result


# Account for display rotation while refusing unsupported non square pixels
def display_size(video):
    width, height = int(video["width"]), int(video["height"])
    if width <= 0 or height <= 0:
        raise ValueError("Source video needs positive dimensions")
    sar = video.get("sample_aspect_ratio")
    if sar not in (None, "N/A", "0:1", "1:1"):
        raise ValueError("Review copies currently require square pixel sources")
    rotation = 0.0
    for row in video.get("side_data_list", []):
        if "rotation" in row:
            rotation = numeric(row["rotation"], "display rotation")
            break
    if abs(rotation / 90 - round(rotation / 90)) > 0.0001:
        raise ValueError("Review copies require a quarter turn display rotation")
    return (height, width) if round(rotation) % 360 in (90, 270) else (width, height)


# Stream every presentation timestamp from a probe file without retaining a frame array
def timestamps(path):
    previous = None
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            value = line.strip().split(",", 1)[0]
            if not value:
                continue
            try:
                timestamp = Fraction(value)
            except (ValueError, ZeroDivisionError) as exc:
                raise ValueError("Video frames need readable presentation timestamps") from exc
            if previous is not None and timestamp <= previous:
                raise ValueError("Video presentation timestamps must increase strictly")
            previous = timestamp
            yield timestamp


# Compare every decoded frame timestamp and retain the constant source clock offset
def compare_timing(source_pts, copy_pts):
    source_first = copy_first = None
    count = 0
    largest = Fraction(0)
    for a, b in itertools.zip_longest(timestamps(source_pts), timestamps(copy_pts)):
        if a is None or b is None:
            raise ValueError("Review copy changed the source frame count")
        if source_first is None:
            source_first, copy_first = a, b
        error = abs((a - source_first) - (b - copy_first))
        if error > Fraction(2, 1_000_000):
            raise ValueError("Review copy changed the source frame timing")
        largest = max(largest, error)
        count += 1
    if count == 0:
        raise ValueError("Source has no decodable video frames")
    return {"frames": count, "source_first_pts": float(source_first), "copy_first_pts": float(copy_first),
            "maximum_timing_error_seconds": float(largest)}


# Measure decoded audio coverage without relying on container duration conventions
def audio_span(path, stream, folder, timeout, label):
    rate = numeric(stream.get("sample_rate"), "audio sample rate")
    if rate <= 0:
        raise ValueError("Audio sample rate must be positive")
    report = Path(folder) / f"audio-{label}.csv"
    with report.open("wb") as output:
        run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
             "frame=best_effort_timestamp_time,nb_samples", "-of", "csv=p=0", path], folder, timeout, output)
    first = last = None
    with report.open(encoding="utf-8") as rows:
        for line in rows:
            if not line.strip():
                continue
            parts = line.strip().split(",")
            timestamp = numeric(parts[0], "audio frame timestamp")
            samples = int(parts[1])
            if samples <= 0:
                raise ValueError("Audio frame needs positive sample count")
            if first is None:
                first = timestamp
            last = timestamp + samples / rate
    if first is None:
        raise ValueError("Audio stream has no decoded samples")
    return first, last


# Prove that the copy remains a timed review derivative of the declared source
def verify(source, output, before, after, folder, timeout, max_edge):
    source_video = next(row for row in before["streams"] if row["codec_type"] == "video")
    copy_video = next(row for row in after["streams"] if row["codec_type"] == "video")
    sw, sh = display_size(source_video)
    cw, ch = display_size(copy_video)
    if max(cw, ch) > max_edge or cw > sw or ch > sh or cw % 2 or ch % 2:
        raise ValueError("Review copy has unexpected dimensions")
    if abs(cw / ch - sw / sh) > 2 / ch:
        raise ValueError("Review copy changed the displayed aspect ratio")
    if copy_video.get("codec_name") != "h264" or copy_video.get("pix_fmt") != "yuv420p":
        raise ValueError("Review copy must be H264 with yuv420p pixels")
    paths = []
    for index, path in enumerate((source, output)):
        pts = Path(folder) / f"timestamps-{index}.csv"
        with pts.open("wb") as stream:
            run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "frame=best_effort_timestamp_time", "-of", "csv=p=0", path], folder, timeout, stream)
        paths.append(pts)
    timing = compare_timing(*paths)
    source_start = numeric(before["format"].get("start_time"), "source start time")
    copy_start = numeric(after["format"].get("start_time"), "copy start time")
    # timeline_view uses input -ss, which is relative to each container's start.
    # Convert between those relative clocks, accounting for first-video offsets.
    # A uniform source PTS delay therefore cancels; subtracting raw first PTS
    # alone would incorrectly seek before the copy starts.
    timing["seek_offset_seconds"] = source_start - timing["source_first_pts"] + timing["copy_first_pts"] - copy_start
    original_audio = next((row for row in before["streams"] if row["codec_type"] == "audio"), None)
    copied_audio = next((row for row in after["streams"] if row["codec_type"] == "audio"), None)
    if bool(original_audio) != bool(copied_audio):
        raise ValueError("Review copy changed audio presence")
    if original_audio is not None:
        original_span = audio_span(source, original_audio, folder, timeout, "source")
        copied_span = audio_span(output, copied_audio, folder, timeout, "copy")
        errors = [abs((a - timing["source_first_pts"]) - (b - timing["copy_first_pts"]))
                  for a, b in zip(original_span, copied_span)]
        if max(errors) > 0.05:
            raise ValueError("Review copy changed the audio and video timing relationship")
        timing["audio_start_error_seconds"], timing["audio_end_error_seconds"] = errors
    run(["ffmpeg", "-v", "error", "-xerror", "-i", output, "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"], folder, timeout)
    return timing


# Validate both files before allowing a saved copy into another inspection tool
def load_verified(manifest, expected_source=None):
    manifest = Path(manifest)
    if manifest.is_symlink() or manifest.parent.is_symlink() or manifest.stat().st_size > 1024 * 1024:
        raise ValueError("Invalid review copy record")
    data = json.loads(manifest.read_bytes())
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("purpose") != "review_only":
        raise ValueError("Unsupported review copy record")
    payload = {key: value for key, value in data.items() if key != "record_sha256"}
    if data.get("record_sha256") != hashlib.sha256(encoded(payload)).hexdigest():
        raise ValueError("Review copy record changed; create a fresh copy before review")
    identity = data["identity"]
    if hashlib.sha256(encoded(identity)).hexdigest() != manifest.parent.name:
        raise ValueError("Review copy identity does not match its cache directory")
    source = Path(identity["source"]).resolve(strict=True)
    if expected_source is not None and source != Path(expected_source).resolve(strict=True):
        raise ValueError("Review copy belongs to a different source")
    movie = manifest.parent / "review.mp4"
    if movie.is_symlink():
        raise ValueError("Review movie cannot be a symbolic link")
    states = [(path, file_state(path)) for path in (source, movie)]
    if sha256(source) != identity["source_sha256"] or sha256(movie) != data["copy_sha256"]:
        raise ValueError("Review copy or source changed; create a fresh copy before review")
    if any(file_state(path) != state for path, state in states):
        raise ValueError("A review input changed while being checked")
    numeric(data["timing"]["seek_offset_seconds"], "review seek offset")
    return {**data, "movie": str(movie.resolve()), "manifest": str(manifest.resolve())}


# Publish a complete verified movie and manifest together in a new cache directory
def make_copy(source, directory, *, max_edge=720, crf=28, tonemap=False, timeout=3600):
    if type(max_edge) is not int or max_edge % 2 or not 64 <= max_edge <= 1920:
        raise ValueError("max edge must be an even integer from 64 to 1920")
    if type(crf) is not int or not 18 <= crf <= 35:
        raise ValueError("crf must be an integer from 18 to 35")
    if not 1 <= numeric(timeout, "timeout") <= 86400:
        raise ValueError("timeout must be between 1 and 86400 seconds")
    source = Path(source).resolve(strict=True)
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("Review cache directory cannot be a symbolic link")
    directory = directory.resolve()
    if source.is_relative_to(directory):
        raise ValueError("Original footage must be outside the review cache directory")
    state = file_state(source)
    identity = {"source": str(source), "source_sha256": sha256(source), "runtime": runtime_identity(),
                "settings": {"max_edge": max_edge, "crf": crf, "tonemap": tonemap}}
    if file_state(source) != state:
        raise ValueError("Source changed while being read")
    key = hashlib.sha256(encoded(identity)).hexdigest()
    target = directory / key
    if target.exists() or target.is_symlink():
        data = load_verified(target / "review.json", source)
        return {**data, "reused": True}
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".review-copy-", dir=directory) as stage_name:
        stage = Path(stage_name)
        before = probe(source, stage, timeout)
        video = next((row for row in before["streams"] if row["codec_type"] == "video"), None)
        if video is None:
            raise ValueError("Source has no video stream")
        display_size(video)
        hdr = video.get("color_transfer") in ("smpte2084", "arib-std-b67")
        if hdr != bool(tonemap):
            raise ValueError("Tagged HDR requires --tonemap; use that flag only for tagged HDR sources")
        filters = []
        if tonemap:
            filters.extend(("zscale=t=linear:npl=100", "format=gbrpf32le", "zscale=p=bt709",
                            "tonemap=tonemap=hable:desat=0", "zscale=t=bt709:m=bt709:r=tv"))
        filters.append(f"scale=w='min({max_edge},iw)':h='min({max_edge},ih)':force_original_aspect_ratio=decrease:force_divisible_by=2")
        movie = stage / "review.mp4"
        args = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-copyts", "-start_at_zero", "-i", source,
                "-map", "0:v:0", "-map", "0:a:0?", "-sn", "-dn", "-vf", ",".join(filters),
                "-fps_mode", "passthrough", "-enc_time_base:v", "1:1000000", "-video_track_timescale", "1000000",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", str(crf), "-pix_fmt", "yuv420p", "-bf", "0", "-g", "30",
                "-c:a", "aac", "-b:a", "96k", "-ar", "48000", "-map_metadata", "-1", "-map_chapters", "-1", "-movflags", "+faststart"]
        if tonemap:
            args += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
        run(args + [movie], stage, timeout)
        after = probe(movie, stage, timeout)
        timing = verify(source, movie, before, after, stage, timeout, max_edge)
        data = {"version": 1, "purpose": "review_only", "identity": identity,
                "copy_sha256": sha256(movie), "timing": timing,
                "source_video": video, "copy_video": next(row for row in after["streams"] if row["codec_type"] == "video")}
        data["record_sha256"] = hashlib.sha256(encoded(data)).hexdigest()
        if file_state(source) != state:
            raise ValueError("Source changed during encoding; review copy was not published")
        (stage / "review.json").write_bytes(encoded(data))
        for path in stage.iterdir():
            if path.name not in ("review.json", "review.mp4"):
                path.unlink()
        try:
            os.rename(stage, target)
        except OSError:
            if target.is_dir() and not target.is_symlink():
                data = load_verified(target / "review.json", source)
                return {**data, "reused": True}
            raise
    return {**data, "manifest": str(target / "review.json"), "movie": str(target / "review.mp4"), "reused": False}


# Expose review copies separately so normal render inputs always remain explicit
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--max-edge", type=int, default=720)
    parser.add_argument("--crf", type=int, default=28)
    parser.add_argument("--tonemap", action="store_true")
    parser.add_argument("--timeout", type=float, default=3600)
    args = parser.parse_args()
    try:
        result = make_copy(args.source, args.cache, max_edge=args.max_edge, crf=args.crf,
                           tonemap=args.tonemap, timeout=args.timeout)
        print(json.dumps(result, indent=2, allow_nan=False))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"review copy: {exc}\n")


if __name__ == "__main__":
    main()
