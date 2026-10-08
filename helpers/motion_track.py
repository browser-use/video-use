#!/usr/bin/env python3
"""Track a user-selected planar region; export measured homographies, never predicted motion.

Optional dependency: video-use[motion-tracking]. FFmpeg/ffprobe decode the source
and provide actual presentation timestamps. OpenCV tracks sparse image features.
"""
from __future__ import annotations

import argparse
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

import numpy as np

try:
    import cv2
except ImportError:
    cv2 = None

DEFAULT_SETTINGS = {"max_features": 300, "min_features": 8, "quality": .01, "min_distance": 5, "fb_threshold": 1.25, "ransac_threshold": 2.5, "min_inlier_ratio": .55, "max_lk_error": 30}


# validate bounded numeric tracking options before decoding media
def finite(value, name, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if (minimum is not None and value < minimum) or (maximum is not None and value > maximum):
        raise ValueError(f"{name} is outside the supported range")
    return value


# explain the optional tracking dependency when it is unavailable
def require_cv():
    if cv2 is None:
        raise ValueError("Planar tracking requires the optional motion-tracking dependency: pip install '.[motion-tracking]'")


# require a finite convex initial region inside the stored frame
def validate_quad(quad, width, height):
    require_cv()
    points = np.asarray(quad, dtype=np.float32)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError("Quad must contain four finite [x,y] points")
    if np.any(points[:, 0] < 0) or np.any(points[:, 0] > width - 1) or np.any(points[:, 1] < 0) or np.any(points[:, 1] > height - 1):
        raise ValueError("Initial quad must lie inside the stored video frame")
    if not cv2.isContourConvex(points) or abs(cv2.contourArea(points)) < 16:
        raise ValueError("Quad must be a nondegenerate convex polygon in perimeter order")
    return points


# convert a rectangular pixel region into its perimeter corners
def roi_quad(value):
    try:
        values = [float(part) for part in value.split(",")]
    except ValueError as error:
        raise ValueError("ROI must be x,y,width,height") from error
    if len(values) != 4:
        raise ValueError("ROI must be x,y,width,height")
    x, y, width, height = values
    for number in values:
        finite(number, "ROI coordinate")
    if width <= 1 or height <= 1:
        raise ValueError("ROI width and height must exceed one pixel")
    return [[x, y], [x + width - 1, y], [x + width - 1, y + height - 1], [x, y + height - 1]]


# track measured planar features and preserve explicit loss without extrapolation
def track_frames(frames, timestamps, quad, *, max_features=300, min_features=8, quality=.01, min_distance=5, fb_threshold=1.25, ransac_threshold=2.5, min_inlier_ratio=.55, max_lk_error=30):
    """Track grayscale uint8 frames; output homographies map frame zero to each frame.

    Once feature evidence is lost, remaining frames are explicitly lost. No
    extrapolation, automatic reacquisition or semantic masking is performed.
    """
    require_cv()
    for name, value, minimum, maximum in [("max features", max_features, 8, 10000), ("min features", min_features, 4, max_features), ("quality", quality, .000001, 1), ("min distance", min_distance, 1, 1000), ("forward-backward threshold", fb_threshold, .01, 100), ("RANSAC threshold", ransac_threshold, .01, 100), ("min inlier ratio", min_inlier_ratio, .01, 1), ("max LK error", max_lk_error, .01, 255)]:
        finite(value, name, minimum, maximum)
    if type(max_features) is not int or type(min_features) is not int:
        raise ValueError("Feature counts must be integers")
    times = list(timestamps)
    if not times or any(isinstance(time, bool) or not isinstance(time, (float, int, Fraction)) or not math.isfinite(time) for time in times):
        raise ValueError("Frame timestamps must be nonempty finite numbers")
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Frame timestamps must be strictly increasing")
    result = []
    previous = None
    source_points = current_points = None
    initial_count = 0
    reference_quad = None
    lost_reason = None
    lk = dict(winSize=(21, 21), maxLevel=3, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 40, .001))
    for index, gray in enumerate(frames):
        if index >= len(times):
            raise ValueError("Decoded more frames than source timestamps")
        if not isinstance(gray, np.ndarray) or gray.dtype != np.uint8 or gray.ndim != 2 or not gray.size:
            raise ValueError("Each tracking frame must be a nonempty grayscale uint8 image")
        height, width = gray.shape
        if previous is not None and gray.shape != previous.shape:
            raise ValueError("Video frame dimensions changed during tracking")
        base = {"index": index, "time": float(times[index] - times[0]), "sourceTime": float(times[index]), "status": "lost", "homography": None, "quad": None, "features": 0, "inliers": 0, "confidence": 0.0}
        if index == 0:
            reference_quad = validate_quad(quad, width, height)
            mask = np.zeros_like(gray)
            cv2.fillConvexPoly(mask, np.round(reference_quad).astype(np.int32), 255)
            detected = cv2.goodFeaturesToTrack(gray, maxCorners=max_features, qualityLevel=quality, minDistance=min_distance, mask=mask, blockSize=7)
            initial_count = 0 if detected is None else len(detected)
            if initial_count < min_features:
                lost_reason = "insufficient_initial_features"
            else:
                source_points = detected.reshape(-1, 2).astype(np.float32)
                current_points = source_points.copy()
                base.update(status="initialized", homography=np.eye(3).tolist(), quad=reference_quad.tolist(), features=initial_count, inliers=initial_count, confidence=1.0, medianReprojectionError=0.0)
        elif lost_reason is None:
            candidates, forward_status, forward_error = cv2.calcOpticalFlowPyrLK(previous, gray, current_points.reshape(-1, 1, 2), None, **lk)
            if candidates is None:
                lost_reason = "optical_flow_failed"
            else:
                backward, backward_status, _ = cv2.calcOpticalFlowPyrLK(gray, previous, candidates, None, **lk)
                if backward is None:
                    lost_reason = "backward_flow_failed"
                else:
                    candidate_points = candidates.reshape(-1, 2)
                    reverse_error = np.linalg.norm(backward.reshape(-1, 2) - current_points, axis=1)
                    accepted = (forward_status.ravel() > 0) & (backward_status.ravel() > 0) & np.isfinite(candidate_points).all(axis=1) & (reverse_error <= fb_threshold) & (forward_error.ravel() <= max_lk_error)
                    accepted &= (candidate_points[:, 0] >= 0) & (candidate_points[:, 0] < width) & (candidate_points[:, 1] >= 0) & (candidate_points[:, 1] < height)
                    anchors, observed = source_points[accepted], candidate_points[accepted]
                    base["features"] = int(len(observed))
                    if len(observed) < min_features:
                        lost_reason = "insufficient_consistent_features"
                    else:
                        homography, inlier_mask = cv2.findHomography(anchors, observed, cv2.RANSAC, ransac_threshold, maxIters=3000, confidence=.995)
                        if homography is None or inlier_mask is None or not np.isfinite(homography).all() or abs(homography[2, 2]) < 1e-10:
                            lost_reason = "homography_estimation_failed"
                        else:
                            inliers = inlier_mask.ravel().astype(bool)
                            count = int(inliers.sum())
                            base["inliers"] = count
                            if count < min_features or count / len(observed) < min_inlier_ratio:
                                lost_reason = "insufficient_homography_inliers"
                            else:
                                homography /= homography[2, 2]
                                mapped = cv2.perspectiveTransform(reference_quad[None], homography)[0]
                                original_area = cv2.contourArea(reference_quad, oriented=True)
                                mapped_area = cv2.contourArea(mapped, oriented=True)
                                coverage = abs(cv2.contourArea(cv2.convexHull(anchors[inliers]))) / abs(original_area)
                                if not np.isfinite(mapped).all() or not cv2.isContourConvex(mapped) or mapped_area * original_area <= 0 or abs(mapped_area) < 16 or np.linalg.cond(homography) > 1e12 or coverage < .02:
                                    lost_reason = "unstable_or_degenerate_plane"
                                else:
                                    reprojection = cv2.perspectiveTransform(anchors[inliers][None], homography)[0]
                                    error = float(np.median(np.linalg.norm(reprojection - observed[inliers], axis=1)))
                                    confidence = (count / initial_count) * math.exp(-error / ransac_threshold)
                                    base.update(status="tracked", homography=homography.tolist(), quad=mapped.tolist(), confidence=round(confidence, 6), medianReprojectionError=round(error, 6), referenceFeatureCoverage=round(coverage, 6))
                                    source_points, current_points = anchors[inliers], observed[inliers]
        if lost_reason is not None:
            base["reason"] = lost_reason
        result.append(base)
        previous = gray
    if len(result) != len(times):
        raise ValueError(f"Decoded {len(result)} frames but found {len(times)} source timestamps")
    return result


# recover real presentation timestamps and rational stream timing
def probe_video(path: Path):
    if not path.is_file():
        raise ValueError(f"Video input does not exist: {path}")
    command = ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams", "-show_frames", "-show_entries", "stream=width,height,avg_frame_rate,r_frame_rate,time_base,duration,duration_ts,start_time,start_pts,sample_aspect_ratio,display_aspect_ratio:frame=best_effort_timestamp,best_effort_timestamp_time", "-of", "json", str(path)]
    result = subprocess.run(command, capture_output=True)
    if result.returncode:
        raise ValueError(f"ffprobe could not inspect video: {result.stderr.decode(errors='replace').strip()}")
    data = json.loads(result.stdout)
    if len(data.get("streams", [])) != 1:
        raise ValueError("Input must contain a decodable video stream")
    metadata = data["streams"][0]
    try:
        time_base = Fraction(metadata["time_base"])
        if time_base <= 0:
            raise ValueError("Nonpositive time base")
    except (KeyError, ValueError, ZeroDivisionError):
        time_base = None
    times = []
    integer_count = 0
    for frame in data.get("frames", []):
        if time_base is not None and type(frame.get("best_effort_timestamp")) is int:
            # Retain the rational value until after subtracting the first PTS.
            # Converting to float first would reintroduce boundary error for
            # sources with a nonzero timestamp offset.
            times.append(frame["best_effort_timestamp"] * time_base)
            integer_count += 1
        elif "best_effort_timestamp_time" in frame:
            times.append(Fraction(frame["best_effort_timestamp_time"]))
        else:
            raise ValueError("A video frame has no presentation timestamp")
    if not times:
        raise ValueError("Input video contains no decoded frames")
    metadata["timestamp_precision"] = "integer_pts" if integer_count == len(times) else "rounded_seconds_fallback" if integer_count == 0 else "mixed_integer_and_rounded"
    stream_duration = None
    if time_base is not None and type(metadata.get("duration_ts")) is int:
        stream_duration = metadata["duration_ts"] * time_base
    elif metadata.get("duration") not in (None, "N/A"):
        stream_duration = Fraction(metadata["duration"])
    if time_base is not None and type(metadata.get("start_pts")) is int:
        stream_start = metadata["start_pts"] * time_base
    elif metadata.get("start_time") not in (None, "N/A"):
        stream_start = Fraction(metadata["start_time"])
    else:
        stream_start = times[0]
    metadata["stream_duration_seconds"] = float(stream_duration) if stream_duration is not None else None
    metadata["stream_start_seconds"] = float(stream_start)
    # The consumer clock begins at the first decoded PTS, not stream timestamp 0.
    metadata["coverage_duration_seconds"] = float(stream_start + stream_duration - times[0]) if stream_duration is not None else None
    return metadata, times


# stream stored grayscale frames without silently rotating their coordinates
def decode_frames(path, width, height):
    # Stored-pixel coordinates are explicit: do not silently rotate ROI geometry.
    command = ["ffmpeg", "-hide_banner", "-v", "error", "-xerror", "-noautorotate", "-i", str(path), "-map", "0:v:0", "-an", "-sn", "-vf", "format=gray", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        while True:
            raw = process.stdout.read(width * height)
            if not raw:
                break
            if len(raw) != width * height:
                raise ValueError("Video decoder returned an incomplete frame")
            yield np.frombuffer(raw, dtype=np.uint8).reshape(height, width)
        error = process.stderr.read().decode(errors="replace")
        if process.wait():
            raise ValueError(f"FFmpeg video decode failed: {error.strip()}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        process.stderr.close()


# combine decoded observations timing and source provenance into one track
def track_video(path: Path, quad, **settings):
    require_cv()
    metadata, times = probe_video(path)
    generator = decode_frames(path, metadata["width"], metadata["height"])
    try:
        frames = track_frames(generator, times, quad, **settings)
    finally:
        generator.close()
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return {"schemaVersion": 1, "method": "sparse pyramidal LK with forward-backward filtering and RANSAC homography", "coordinateSpace": "stored video pixels without metadata autorotation; origin top left", "homographyDirection": "reference frame pixels to current frame pixels", "referenceFrame": 0, "initialQuad": np.asarray(quad, dtype=float).tolist(), "source": {"file": path.name, "sha256": digest.hexdigest(), "width": metadata["width"], "height": metadata["height"], "frameCount": len(frames), "avgFrameRate": metadata.get("avg_frame_rate"), "rFrameRate": metadata.get("r_frame_rate"), "timeBase": metadata.get("time_base"), "sampleAspectRatio": metadata.get("sample_aspect_ratio"), "displayAspectRatio": metadata.get("display_aspect_ratio"), "firstTimestamp": float(times[0]), "lastTimestamp": float(times[-1]), "timestampPrecision": metadata["timestamp_precision"], "duration": metadata["coverage_duration_seconds"], "streamDuration": metadata["stream_duration_seconds"], "streamStartTime": metadata["stream_start_seconds"]}, "settings": {**DEFAULT_SETTINGS, **settings}, "frames": frames}


# track the selected plane and write measured geometry for compositing
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("-o", "--output", type=Path, required=True)
    region = parser.add_mutually_exclusive_group(required=True)
    region.add_argument("--quad", help="Four perimeter-ordered points as JSON, e.g. '[[20,20],[180,20],[180,130],[20,130]]'")
    region.add_argument("--roi", help="Initial rectangle x,y,width,height in stored pixels")
    parser.add_argument("--max-features", type=int, default=300)
    parser.add_argument("--min-features", type=int, default=8)
    parser.add_argument("--quality", type=float, default=.01)
    parser.add_argument("--min-distance", type=float, default=5)
    parser.add_argument("--fb-threshold", type=float, default=1.25)
    parser.add_argument("--ransac-threshold", type=float, default=2.5)
    parser.add_argument("--min-inlier-ratio", type=float, default=.55)
    parser.add_argument("--max-lk-error", type=float, default=30)
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.output.resolve():
            raise ValueError("Track output must not overwrite source video")
        if args.output.exists() or args.output.is_symlink():
            raise ValueError("Track output already exists; choose a new output path")
        quad = json.loads(args.quad) if args.quad else roi_quad(args.roi)
        settings = {name: getattr(args, name) for name in ("max_features", "min_features", "quality", "min_distance", "fb_threshold", "ransac_threshold", "min_inlier_ratio", "max_lk_error")}
        data = track_video(args.input, quad, **settings)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        serialized = json.dumps(data, separators=(",", ":"), allow_nan=False) + "\n"
        with args.output.open("x") as output:
            output.write(serialized)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    lost = sum(frame["status"] == "lost" for frame in data["frames"])
    print(f"Analyzed {len(data['frames'])} frames; {lost} explicitly lost: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
