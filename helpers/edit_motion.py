"""Plan authored picture clocks and register reviewed poses without touching media.

Times are exact rational seconds. Coordinates address pixel centers, with (0, 0)
at the top-left pixel center. No audio, decoder, tracking or optical flow is used.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from fractions import Fraction
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image


def _time(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Fraction)):
        raise ValueError(f"{name} must be a finite number or rational string")
    if isinstance(value, str) and (len(value) > 80 or not re.fullmatch(
        r"[+-]?(?:\d+/[1-9]\d*|\d+(?:\.\d*)?|\.\d+)", value
    )):
        raise ValueError(f"{name} must use decimal seconds or numerator/denominator")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    try:
        result = value if isinstance(value, Fraction) else Fraction(str(value))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError(f"Invalid {name}") from error
    if abs(result) > 86400:
        raise ValueError(f"{name} must lie within +/-86400 seconds")
    return result


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{name} must be an integer in [{low}, {high}]")
    return value


def _keys(value, required, optional, name):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError(f"{name} requires {sorted(required)}; optional keys: {sorted(optional)}")


def plan_clock(timestamps, source_end, segments, fps=30, *, audio_policy):
    """Return a JSON-ready output-frame -> real source-frame sampling plan.

    ``timestamps`` are strictly increasing display-order PTS, in seconds on ONE
    explicit source clock. A frame covers [its PTS, next PTS); the last covers
    [last PTS, source_end). No average frame-rate inference or end clamping occurs.

    A segment requires ``frames``, ``source_start``, ``speed_start``, ``speed_end``.
    Its instantaneous source/output rate varies linearly across frames/fps seconds.
    The integral, not the speed itself, supplies source time. Zero/zero is a hold.
    Samples exclude the segment's end boundary; a following continuous segment
    starts AT that boundary. Discontinuities require ``cut_before: true``.

    ``audio_policy`` must explicitly be 'silent' or 'separate'. This is a picture
    plan only; it never retimes source audio or translates dialogue timestamps.
    """
    if audio_policy not in ("silent", "separate"):
        raise ValueError("audio_policy must be 'silent' or 'separate'; no dialogue retiming")
    rate = _time(fps, "fps")
    if not 1 <= rate <= 120:
        raise ValueError("fps must lie in [1, 120]")
    if not isinstance(timestamps, (list, tuple)) or not 1 <= len(timestamps) <= 1_000_000:
        raise ValueError("Supply 1..1000000 actual display-order timestamps")
    pts = [_time(t, "source timestamp") for t in timestamps]
    if any(a >= b for a, b in zip(pts, pts[1:])):
        raise ValueError("Source timestamps must be strictly increasing")
    end = _time(source_end, "source_end")
    if end <= pts[-1]:
        raise ValueError("source_end must be strictly after the last source PTS")
    if not isinstance(segments, (list, tuple)) or not 1 <= len(segments) <= 1000:
        raise ValueError("Supply 1..1000 authored clock segments")
    rows, authored, previous_end = [], [], None
    for index, segment in enumerate(segments):
        _keys(segment, ("frames", "source_start", "speed_start", "speed_end"), ("cut_before",), "segment")
        count = _integer(segment["frames"], "frames", 1, 14400)
        if (len(rows) + count) / rate > 120:
            raise ValueError("Output picture clock must be at most 120 seconds")
        start = _time(segment["source_start"], "source_start")
        v0, v1 = (_time(segment[k], k) for k in ("speed_start", "speed_end"))
        if not 0 <= v0 <= 16 or not 0 <= v1 <= 16:
            raise ValueError("Endpoint speeds must lie in [0, 16]; reverse is unsupported")
        cut = segment.get("cut_before", False)
        if type(cut) is not bool or (index == 0 and cut):
            raise ValueError("cut_before must be boolean and cannot precede the first segment")
        if previous_end is not None and start != previous_end and not cut:
            raise ValueError(f"Discontinuous source_start: expected {previous_end}; declare cut_before")
        duration = count / rate
        boundary = start + duration * (v0 + v1) / 2
        if not pts[0] <= start < end or boundary > end:
            raise ValueError("Segment lies outside the measured source timeline")
        offset = len(rows)
        authored.append({"output_start_frame": offset, "frames": count, "source_start": str(start),
                         "source_end": str(boundary), "speed_start": str(v0), "speed_end": str(v1),
                         "cut_before": cut})
        for local in range(count):
            elapsed = Fraction(local, 1) / rate
            requested = start + v0 * elapsed + (v1 - v0) * elapsed * elapsed / (2 * duration)
            if not pts[0] <= requested < end:
                raise ValueError("Sample lies outside the half-open source timeline")
            selected = bisect_right(pts, requested) - 1
            rows.append({"output_frame": offset + local, "output_time": str(Fraction(offset + local, 1) / rate),
                         "source_time": str(requested), "source_frame": selected,
                         "source_pts": str(pts[selected]),
                         "source_frame_end": str(pts[selected + 1] if selected + 1 < len(pts) else end)})
        previous_end = boundary
    return {"schema": "video-use.edit-motion-clock.v1", "fps": str(rate), "duration": str(len(rows) / rate),
            "audio_policy": audio_policy, "audio_processed": False,
            "sampling": "Containing display interval; frame repeat/drop, no optical flow",
            "source": {"frames": len(pts), "first_pts": str(pts[0]), "end": str(end)},
            "segments": authored, "frames": rows}


def _size(value, name):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{name} must be [width, height]")
    size = tuple(_integer(v, name, 2, 16384) for v in value)
    if size[0] * size[1] > 33_554_432:
        raise ValueError(f"{name} exceeds 33554432 pixels")
    return size


def _points(value, size, name, minimum):
    try:
        raw = np.asarray(value)
        if raw.dtype.kind not in "iuf" or any(isinstance(v, (bool, np.bool_)) for v in np.asarray(value, dtype=object).flat):
            raise ValueError("Non-numeric landmark")
        points = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must contain finite [x, y] pairs") from error
    if points.ndim != 2 or points.shape[1] != 2 or not minimum <= len(points) <= 128 or not np.isfinite(points).all():
        raise ValueError(f"{name} requires {minimum}..128 finite landmark pairs")
    if np.any(points < 0) or np.any(points > np.array(size) - 1):
        raise ValueError(f"{name} landmarks lie outside pixel-center bounds")
    distances = np.linalg.norm(points[:, None] - points[None, :], axis=2)
    if np.any(distances[np.triu_indices(len(points), 1)] < 1e-6):
        raise ValueError(f"{name} contains duplicate or degenerate landmarks")
    if np.linalg.norm(points - points.mean(axis=0)) < 1:
        raise ValueError(f"{name} landmark spread is too small for stable registration")
    return points


def _anchor(value):
    _keys(value, ("frame", "timestamp"), ("sha256",), "source_anchor")
    result = {"frame": _integer(value["frame"], "anchor frame", 0, 1_000_000_000),
              "timestamp": str(_time(value["timestamp"], "anchor timestamp"))}
    if "sha256" in value:
        if not isinstance(value["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"]):
            raise ValueError("Anchor sha256 must be a lowercase SHA256")
        result["sha256"] = value["sha256"]
    return result


def _matrix(value):
    try:
        matrix = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError("Invalid source-to-target matrix") from error
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or not np.array_equal(matrix[2], [0, 0, 1]):
        raise ValueError("Expected finite affine 3x3 matrix with last row [0, 0, 1]")
    singular = np.linalg.svd(matrix[:2, :2], compute_uv=False)
    if np.linalg.det(matrix[:2, :2]) <= 0 or singular[-1] < 1 / 16 - 1e-10 or singular[0] > 16 + 1e-10 or singular[0] / singular[-1] > 16:
        raise ValueError("Reflection, degenerate transform or scale outside [1/16, 16] / anisotropy >16")
    if np.max(np.abs(matrix[:2, 2])) > 262144:
        raise ValueError("Translation exceeds supported geometry bounds")
    return matrix


def fit_pose(source_landmarks, target_landmarks, source_size, target_size, source_anchor,
             model="similarity", *, max_error=2.0):
    """Fit ordered, manually reviewed corresponding landmarks to a target canvas.

    Similarity: >=2 distinct pairs, uniform scale + rotation + translation.
    Affine: >=3 noncollinear pairs, also permits shear/nonuniform scale. Neither
    reflects, tracks, deforms individual body parts or proves a seamless cut.
    ``max_error`` bounds EVERY residual in target pixels, not just average error.
    The anchor frame/PTS (and optional source hash) is recorded, not media-probed.
    """
    if model not in ("similarity", "affine"):
        raise ValueError("model must be 'similarity' or 'affine'")
    if isinstance(max_error, bool) or not isinstance(max_error, (int, float)) or not math.isfinite(max_error) or not 0 <= max_error <= 64:
        raise ValueError("max_error must be finite and in [0, 64] target pixels")
    source_size, target_size = _size(source_size, "source_size"), _size(target_size, "target_size")
    anchor = _anchor(source_anchor)
    minimum = 2 if model == "similarity" else 3
    source = _points(source_landmarks, source_size, "source", minimum)
    target = _points(target_landmarks, target_size, "target", minimum)
    if source.shape != target.shape:
        raise ValueError("Source and target landmarks must correspond one-to-one")
    x, y = source - source.mean(axis=0), target - target.mean(axis=0)
    if model == "similarity":
        u, singular, vt = np.linalg.svd(x.T @ y)
        sign = np.array([1.0, 1.0 if np.linalg.det(vt.T @ u.T) > 0 else -1.0])
        rotation = (vt.T * sign) @ u.T
        scale = float(singular @ sign / np.sum(x * x))
        linear = scale * rotation
    else:
        for points in (x, y):
            singular = np.linalg.svd(points, compute_uv=False)
            if singular[-1] <= singular[0] * 1e-6:
                raise ValueError("Affine landmarks are collinear or ill-conditioned")
        linear = np.linalg.lstsq(x, y, rcond=None)[0].T
    matrix = np.eye(3)
    matrix[:2, :2] = linear
    matrix[:2, 2] = target.mean(axis=0) - linear @ source.mean(axis=0)
    matrix = _matrix(matrix)
    fitted = source @ matrix[:2, :2].T + matrix[:2, 2]
    residuals = np.linalg.norm(fitted - target, axis=1)
    if np.max(residuals) > max_error + 1e-8:
        raise ValueError(f"Pose residual {np.max(residuals):.6g}px exceeds max_error {max_error}")
    return {"schema": "video-use.edit-motion-pose.v1", "model": model,
            "pixel_convention": "integer pixel centers; x right, y down; column-vector matrices",
            "source_size": list(source_size), "target_size": list(target_size), "source_anchor": anchor,
            "source_landmarks": source.tolist(), "target_landmarks": target.tolist(),
            "source_to_target": matrix.tolist(), "target_to_source": np.linalg.inv(matrix).tolist(),
            "residuals_px": residuals.tolist(), "max_error_px": float(np.max(residuals)),
            "rms_error_px": float(np.sqrt(np.mean(residuals ** 2))), "error_limit_px": float(max_error)}


def warp_pose(image, pose, *, resample="bilinear"):
    """Warp an L matte, RGB frame or RGBA layer with the SAME checked pose map.

    Returns a new Pillow image; input pixels are untouched. Uncovered pixels are
    zero (transparent for RGBA). Convert RGB to RGBA first to retain coverage.
    Pillow's corner-based inverse sampling receives an explicit half-pixel
    correction from our center-based forward matrix. RGBA filters premultiplied
    color, preserving the alpha/matte geometry without dark interpolation fringes.
    """
    if not isinstance(pose, dict) or pose.get("schema") != "video-use.edit-motion-pose.v1":
        raise ValueError("Expected a fit_pose record")
    if pose.get("pixel_convention") != "integer pixel centers; x right, y down; column-vector matrices":
        raise ValueError("Unsupported pixel convention")
    source_size, target_size = _size(pose.get("source_size"), "source_size"), _size(pose.get("target_size"), "target_size")
    if not isinstance(image, Image.Image) or image.mode not in ("L", "RGB", "RGBA") or image.size != source_size:
        raise ValueError("Image must be an L/RGB/RGBA source-sized Pillow image")
    # Bilinear is bounded: cubic ringing can overshoot premultiplied color/alpha
    # independently at a matte edge. Keep this small compositor predictable.
    methods = {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR}
    if resample not in methods:
        raise ValueError("resample must be nearest or bilinear")
    matrix = _matrix(pose.get("source_to_target"))
    inverse = np.linalg.inv(matrix)
    recorded = np.asarray(pose.get("target_to_source"), dtype=float)
    if recorded.shape != (3, 3) or not np.isfinite(recorded).all() or not np.allclose(inverse, recorded, rtol=0, atol=1e-9):
        raise ValueError("Pose inverse does not match the forward map")
    inverse[:2, 2] += .5 - inverse[:2, :2] @ np.array([.5, .5])
    coefficients = tuple(inverse[:2].ravel())
    mode = image.mode
    premultiply = mode == "RGBA" and resample != "nearest"
    working = image.convert("RGBa") if premultiply else image
    result = working.transform(target_size, Image.Transform.AFFINE, coefficients, resample=methods[resample], fillcolor=0)
    return result.convert("RGBA") if premultiply else result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("clock", "pose"))
    parser.add_argument("spec", type=Path, help="JSON keyword arguments for plan_clock or fit_pose")
    args = parser.parse_args(argv)
    try:
        if args.spec.stat().st_size > 32_000_000:
            raise ValueError("Specification exceeds 32 MB")
        spec = json.loads(args.spec.read_text())
        if not isinstance(spec, dict):
            raise ValueError("Specification must be an object")
        result = (plan_clock if args.operation == "clock" else fit_pose)(**spec)
    except (OSError, TypeError, ValueError, np.linalg.LinAlgError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
