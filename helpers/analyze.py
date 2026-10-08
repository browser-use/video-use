"""Reuse source metadata and timestamped picture or silence measurements."""

import argparse
import json
import math
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

try:
    from .analysis_cache import AnalysisCache
    from .edit_io import sha256
except ImportError:
    from analysis_cache import AnalysisCache
    from edit_io import sha256


NEEDS = ("metadata", "scenes", "motion", "silence")


# Include tool builds and provider implementation bytes in the saved result identity
def runtime_identity():
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        path = shutil.which(name)
        if path is None:
            raise ValueError(f"Install {name} before analyzing media")
        result = subprocess.run([path, "-version"], capture_output=True, check=True, timeout=20)
        tools[name] = {"path": str(Path(path).resolve()), "version": result.stdout.decode(errors="replace")}
    folder = Path(__file__).parent
    return {"tools": tools, "python": platform.python_version(),
            "code": {name: sha256(folder / name) for name in ("analyze.py", "analysis_cache.py", "edit_io.py")}}


# Validate settings before hashing sources or starting any media command
def number(value, name, minimum, maximum):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and minimum <= value <= maximum
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError(f"{name} must be a finite number from {minimum} to {maximum}")
    return value


# Read one compact source manifest without decoding its frames
def metadata(source, timeout):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
                            capture_output=True, check=True, timeout=timeout)
    data = json.loads(result.stdout)
    keys = ("index", "codec_type", "codec_name", "width", "height", "sample_rate", "channels",
            "avg_frame_rate", "r_frame_rate", "time_base", "start_time", "duration", "pix_fmt",
            "color_range", "color_space", "color_transfer", "color_primaries", "side_data_list")
    streams = [{key: row[key] for key in keys if key in row} for row in data["streams"]]
    for original, selected in zip(data["streams"], streams):
        if "rotate" in original.get("tags", {}):
            selected["tags"] = {"rotate": original["tags"]["rotate"]}
    fmt = data.get("format", {})
    return {"streams": streams, "format": {key: fmt[key] for key in ("format_name", "start_time", "duration", "size") if key in fmt}}


# Run a bounded analysis command with disk backed logs and actionable failures
def ffmpeg(args, folder, timeout):
    log = Path(folder) / "ffmpeg.log"
    with log.open("wb") as stderr:
        try:
            result = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-nostdin", *args],
                                    cwd=folder, stdout=subprocess.DEVNULL, stderr=stderr, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"Analysis exceeded {timeout:g}s; increase --timeout for longer footage") from exc
    if result.returncode:
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 4000))
            detail = stream.read().decode(errors="replace")
        raise ValueError(f"FFmpeg analysis failed: {detail}")
    return log


# Measure scene changes and short motion windows in one sequential low resolution pass
def picture(source, scene_threshold, motion_window, timeout):
    scenes, windows = [], {}
    previous = None
    with tempfile.TemporaryDirectory(prefix="video-use-picture-analysis-") as folder:
        ffmpeg(["-v", "error", "-copyts", "-i", str(source), "-map", "0:v:0", "-an", "-sn", "-dn",
                "-vf", "scale=160:160:force_original_aspect_ratio=decrease,select='gte(scene,0)',metadata=print:file=events.txt",
                "-fps_mode", "passthrough", "-f", "null", "-"], folder, timeout)
        timestamp = None
        count = 0
        with Path(folder, "events.txt").open(encoding="utf-8") as stream:
            for line in stream:
                if line.startswith("frame:"):
                    match = re.search(r"pts_time:([^\s]+)", line)
                    timestamp = float(match.group(1)) if match else None
                elif line.startswith("lavfi.scene_score="):
                    score = float(line.split("=", 1)[1])
                    if timestamp is None or not math.isfinite(timestamp) or not math.isfinite(score):
                        raise ValueError("Picture analysis needs finite presentation timestamps and scores")
                    if previous is not None and timestamp < previous:
                        raise ValueError("Picture timestamps must not go backwards")
                    previous = timestamp
                    if score >= scene_threshold:
                        scenes.append({"time": timestamp, "score": score, "frame": count})
                    bucket = math.floor(timestamp / motion_window)
                    row = windows.setdefault(bucket, {"start": bucket * motion_window,
                                                      "end": (bucket + 1) * motion_window, "peak_change": 0.0, "samples": 0})
                    row["peak_change"] = max(row["peak_change"], score)
                    row["samples"] += 1
                    count += 1
    if count == 0:
        raise ValueError("No video frames were measured")
    return {"time_base": "source presentation timestamps in seconds", "frames_measured": count,
            "scenes": scenes, "motion": list(windows.values())}


# Detect first audio stream silence and retain its native presentation timestamps
def silence(source, noise_db, minimum, timeout):
    intervals = []
    start = None
    with tempfile.TemporaryDirectory(prefix="video-use-silence-analysis-") as folder:
        log = ffmpeg(["-v", "info", "-copyts", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
                      "-af", f"silencedetect=noise={noise_db}dB:d={minimum}", "-f", "null", "-"], folder, timeout)
        with log.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                for kind, value in re.findall(r"silence_(start|end):\s*([-+0-9.eE]+)", line):
                    value = float(value)
                    if not math.isfinite(value):
                        raise ValueError("Silence analysis returned a nonfinite timestamp")
                    if kind == "start":
                        start = value
                    elif start is not None:
                        if value < start:
                            raise ValueError("Silence interval ended before it began")
                        intervals.append({"start": start, "end": value})
                        start = None
    if start is not None:
        raise ValueError("Silence analysis ended without a closing timestamp")
    return {"time_base": "source presentation timestamps in seconds", "intervals": intervals}


# Resolve requested evidence while independently reusing intact provider records
def analyze(source, directory, needs=("metadata",), *, scene_threshold=0.3, motion_window=1.0,
            noise_db=-35.0, minimum_silence=0.4, timeout=600.0, runtime=None):
    needs = set(needs)
    if not needs or not needs <= set(NEEDS):
        raise ValueError("Choose metadata scenes motion or silence")
    number(scene_threshold, "scene threshold", 0.001, 1)
    number(motion_window, "motion window", 0.1, 60)
    number(noise_db, "silence noise", -100, 0)
    number(minimum_silence, "minimum silence", 0.01, 60)
    number(timeout, "timeout", 1, 86400)
    cache = AnalysisCache(source, directory, runtime_identity() if runtime is None else runtime)
    meta, receipt = cache.get("metadata", {}, lambda: metadata(cache.source, timeout))
    results, reuse = {"metadata": meta}, {"metadata": receipt}
    types = {row.get("codec_type") for row in meta["streams"]}
    if needs & {"scenes", "motion"}:
        if "video" not in types:
            raise ValueError("Scene and motion analysis require a video stream")
        measured, receipt = cache.get("picture", {"scene_threshold": scene_threshold, "motion_window": motion_window},
                                      lambda: picture(cache.source, scene_threshold, motion_window, timeout))
        results["picture"], reuse["picture"] = measured, receipt
    if "silence" in needs:
        if "audio" in types:
            measured, receipt = cache.get("silence", {"noise_db": noise_db, "minimum_silence": minimum_silence},
                                          lambda: silence(cache.source, noise_db, minimum_silence, timeout))
            results["silence"], reuse["silence"] = measured, receipt
        else:
            results["silence"] = {"intervals": [], "note": "Source has no audio stream"}
    cache.check_source()
    return {"source": str(cache.source), "source_sha256": cache.digest, "requested": sorted(needs),
            "results": results, "reuse": reuse}


# Keep dense measurements on disk and return only the requested time window
def summary(report, start=None, end=None, limit=20):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("limit must be an integer from 1 to 1000")
    for value in (start, end):
        if value is not None:
            number(value, "query time", -86400, 864000)
    if start is not None and end is not None and end <= start:
        raise ValueError("query end must be after start")
    result = {key: report[key] for key in ("source", "source_sha256", "reuse")}
    result["metadata"] = report["results"]["metadata"]
    for name in report["requested"]:
        if name == "metadata":
            continue
        data = report["results"].get("silence" if name == "silence" else "picture", {})
        rows = data.get("intervals" if name == "silence" else name, [])
        selected = [row for row in rows if (end is None or row.get("time", row.get("start")) < end)
                    and (start is None or (row["time"] >= start if "time" in row else row["end"] > start))]
        result[name] = {"matching": len(selected), "shown": selected[:limit], "time_base": data.get("time_base"),
                        **({"note": data["note"]} if "note" in data else {})}
    return result


# Expose explicit analyses and compact timestamp queries without changing an edit
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--cache", required=True, type=Path)
    parser.add_argument("--need", action="append", choices=NEEDS)
    parser.add_argument("--scene-threshold", type=float, default=0.3)
    parser.add_argument("--motion-window", type=float, default=1.0)
    parser.add_argument("--noise-db", type=float, default=-35.0)
    parser.add_argument("--minimum-silence", type=float, default=0.4)
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--start", type=float)
    parser.add_argument("--end", type=float)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    try:
        # Validate query bounds before any analysis work
        summary({"source": "", "source_sha256": "", "reuse": {}, "requested": [], "results": {"metadata": {}}}, args.start, args.end, args.limit)
        result = analyze(args.source, args.cache, args.need or ["metadata"], scene_threshold=args.scene_threshold,
                         motion_window=args.motion_window, noise_db=args.noise_db,
                         minimum_silence=args.minimum_silence, timeout=args.timeout)
        print(json.dumps(summary(result, args.start, args.end, args.limit), indent=2, allow_nan=False))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"analysis: {exc}\n")


if __name__ == "__main__":
    main()
