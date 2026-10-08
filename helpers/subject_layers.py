"""Source-bound frozen-image layers; no segmentation, footage decoding or styling.

Files/PNG output use straight alpha. Array primitives use float32 premultiplied
RGBA in encoded RGB space. Integer frame intervals are half-open. Pillow, NumPy
and the standard library are the only dependencies; see subject-layers.md.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import re

import numpy as np
from PIL import Image

MAX_PIXELS = 8_388_608
MAX_ASSET_PIXELS = 67_108_864
RESAMPLING = {"nearest": Image.Resampling.NEAREST,
              "bilinear": Image.Resampling.BILINEAR,
              "bicubic": Image.Resampling.BICUBIC}
POSE_FIELDS = {"position", "scale", "rotation", "opacity"}


def _fields(value, allowed, label):
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{label} must be an object with only {sorted(allowed)}")


def _number(value, low, high, label):
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not low <= value <= high):
        raise ValueError(f"{label} must be finite and between {low} and {high}")
    return float(value)


def _integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer between {low} and {high}")
    return value


def _pair(value, low, high, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must contain two numbers")
    return [_number(v, low, high, label) for v in value]


def _size(value, label):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must be [width, height]")
    result = [_integer(v, 1, 8192, label) for v in value]
    if result[0] * result[1] > MAX_PIXELS:
        raise ValueError(f"{label} exceeds {MAX_PIXELS} pixels")
    return result


def _digest(value, label):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError(f"{label} must be a lowercase SHA256")
    return value


def _pose(value):
    _fields(value, POSE_FIELDS, "pose")
    return {"position": _pair(value.get("position", [0, 0]), -65536, 65536, "position"),
            "scale": _pair(value.get("scale", [1, 1]), 1/256, 256, "scale"),
            "rotation": _number(value.get("rotation", 0), -36000, 36000, "rotation"),
            "opacity": _number(value.get("opacity", 1), 0, 1, "opacity")}


def _motion(value, size, interval, frame_count):
    anchor = _pair(value.get("anchor", [0, 0]), 0, max(size), "anchor")
    if anchor[0] > size[0] or anchor[1] > size[1]:
        raise ValueError("anchor must be inside the source image bounds")
    if "pose" in value and "keyframes" in value:
        raise ValueError("Use either a static pose or keyframes")
    keys = value.get("keyframes")
    motion = {"anchor": anchor}
    if keys is None:
        motion["pose"] = _pose(value.get("pose", {}))
        return motion
    if not isinstance(keys, list) or not 2 <= len(keys) <= 4096:
        raise ValueError("keyframes needs 2 through 4096 complete poses")
    parsed = []
    for key in keys:
        _fields(key, POSE_FIELDS | {"frame", "ease"}, "keyframe")
        at = _integer(key.get("frame"), 0, frame_count, "keyframe.frame")
        if parsed and at <= parsed[-1]["frame"]:
            raise ValueError("keyframe indices must be strictly increasing")
        ease = key.get("ease", "linear")
        if not isinstance(ease, str) or ease not in {"linear", "smoothstep", "hold"}:
            raise ValueError("ease must be linear, smoothstep or hold")
        parsed.append({"frame": at, "ease": ease,
                       **_pose({k: v for k, v in key.items() if k in POSE_FIELDS})})
    if parsed[0]["frame"] > interval[0] or parsed[-1]["frame"] < interval[1] - 1:
        raise ValueError("keyframes must cover every active frame; no extrapolation")
    motion["keyframes"] = parsed
    return motion


def validate_config(config):
    """Return a normalized copy; reject ambiguous files, geometry and clocks."""
    _fields(config, {"version", "canvas", "fps", "frame_count", "background",
                     "resample", "assets", "layers"}, "scene")
    if type(config.get("version")) is not int or config["version"] != 1:
        raise ValueError("version must be 1")
    result = {"version": 1, "canvas": _size(config.get("canvas"), "canvas"),
              "fps": _number(config.get("fps"), .01, 120, "fps"),
              "frame_count": _integer(config.get("frame_count"), 1, 72000, "frame_count")}
    if result["frame_count"] / result["fps"] > 600:
        raise ValueError("Scene duration exceeds 600 seconds")
    color = config.get("background", [0, 0, 0, 0])
    if not isinstance(color, list) or len(color) != 4:
        raise ValueError("background must contain four straight RGBA bytes")
    result["background"] = [_integer(v, 0, 255, "background") for v in color]
    result["resample"] = config.get("resample", "bilinear")
    if not isinstance(result["resample"], str) or result["resample"] not in RESAMPLING:
        raise ValueError("resample must be nearest, bilinear or bicubic")
    assets = config.get("assets")
    if not isinstance(assets, dict) or not 1 <= len(assets) <= 128:
        raise ValueError("assets needs 1 through 128 named images/masks")
    parsed, pixels = {}, 0
    for name, item in assets.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", name):
            raise ValueError("Asset names need 1–80 letters, digits, hyphens or underscores")
        _fields(item, {"path", "sha256", "size", "kind", "source_sha256", "source_frame"}, "asset")
        path = item.get("path")
        if (not isinstance(path, str) or not path or "\\" in path or "\x00" in path or Path(path).is_absolute()
                or any(part in {"", ".", ".."} for part in path.split("/"))):
            raise ValueError("Asset paths must be explicit relative paths within the scene directory")
        kind = item.get("kind")
        if not isinstance(kind, str) or kind not in {"image", "mask"}:
            raise ValueError("Asset kind must be image or mask")
        entry = {"path": path, "sha256": _digest(item.get("sha256"), "asset.sha256"),
                 "size": _size(item.get("size"), "asset.size"), "kind": kind}
        if "source_frame" in item:
            entry["source_frame"] = _integer(item["source_frame"], 0, 2**31 - 1, "source_frame")
        if kind == "mask":
            entry["source_sha256"] = _digest(item.get("source_sha256"), "mask.source_sha256")
        elif "source_sha256" in item:
            raise ValueError("source_sha256 belongs on a mask and binds encoded image-file bytes")
        pixels += entry["size"][0] * entry["size"][1]
        parsed[name] = entry
    if pixels > MAX_ASSET_PIXELS:
        raise ValueError("Combined assets exceed the pixel budget")
    for entry in parsed.values():
        if entry["kind"] == "mask":
            sources = [v for v in parsed.values() if v["kind"] == "image"
                       and v["sha256"] == entry["source_sha256"]]
            if not sources or any(v["size"] != entry["size"] for v in sources):
                raise ValueError("Every mask must bind a present image with identical dimensions")
            if "source_frame" in entry and any(v.get("source_frame") != entry["source_frame"] for v in sources):
                raise ValueError("Mask source_frame differs from its bound image")
    result["assets"] = parsed
    layers = config.get("layers")
    if not isinstance(layers, list) or not 1 <= len(layers) <= 128:
        raise ValueError("layers needs 1 through 128 ordered layers")
    result["layers"], ids, captions_started = [], set(), False
    for layer in layers:
        _fields(layer, {"id", "image", "mask", "frames", "anchor", "pose",
                        "keyframes", "reveal", "role"}, "layer")
        name = layer.get("id")
        if not isinstance(name, str) or not name or len(name) > 200 or name in ids:
            raise ValueError("Each layer needs a unique nonempty id of at most 200 characters")
        ids.add(name)
        image = parsed.get(layer["image"]) if isinstance(layer.get("image"), str) else None
        if image is None or image["kind"] != "image":
            raise ValueError("Layer image must reference an image asset")
        frames = layer.get("frames")
        if not isinstance(frames, list) or len(frames) != 2:
            raise ValueError("Layer frames must be a half-open [start, end] pair")
        start, end = [_integer(n, 0, result["frame_count"], "layer frame") for n in frames]
        if start >= end:
            raise ValueError("Layer must have at least one active frame")
        role = layer.get("role", "image")
        if not isinstance(role, str) or role not in {"image", "caption"} or (captions_started and role != "caption"):
            raise ValueError("Dialogue captions must be last; use image for occluded artwork/type")
        captions_started |= role == "caption"
        entry = {"id": name, "image": layer["image"], "frames": [start, end], "role": role,
                 **_motion(layer, image["size"], frames, result["frame_count"])}
        if "mask" in layer:
            mask = parsed.get(layer["mask"]) if isinstance(layer["mask"], str) else None
            if mask is None or mask["kind"] != "mask" or mask["source_sha256"] != image["sha256"]:
                raise ValueError("Cutout mask must bind this layer's source image")
            entry["mask"] = layer["mask"]
        if "reveal" in layer:
            reveal = layer["reveal"]
            _fields(reveal, {"mask", "invert", "anchor", "pose", "keyframes"}, "reveal")
            mask = parsed.get(reveal["mask"]) if isinstance(reveal.get("mask"), str) else None
            if mask is None or mask["kind"] != "mask":
                raise ValueError("Reveal must reference a source-bound mask asset")
            if type(reveal.get("invert", False)) is not bool:
                raise ValueError("reveal.invert must be boolean")
            entry["reveal"] = {"mask": reveal["mask"], "invert": reveal.get("invert", False),
                               **_motion(reveal, mask["size"], frames, result["frame_count"])}
        result["layers"].append(entry)
    return result


def premultiply(image):
    """PIL RGB/RGBA or uint8 H×W×3/4 → independent premultiplied float RGBA."""
    if isinstance(image, Image.Image):
        if image.mode not in {"RGB", "RGBA"}:
            raise ValueError("Image must use RGB or straight RGBA")
        image = np.asarray(image)
    array = np.asarray(image)
    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] not in {3, 4}:
        raise ValueError("Image buffer must be uint8 H×W×3/4 straight RGB/RGBA")
    _size([array.shape[1], array.shape[0]], "image buffer")
    output = np.ones((*array.shape[:2], 4), np.float32)
    output[..., :array.shape[2]] = array.astype(np.float32) / 255
    output[..., :3] *= output[..., 3:4]
    return output


def _premult(array):
    array = np.asarray(array)
    if (array.ndim != 3 or array.shape[2] != 4 or array.dtype.kind != "f"
            or not np.isfinite(array).all() or np.any(array < 0)
            or np.any(array > 1) or np.any(array[..., :3] > array[..., 3:4] + 1e-6)):
        raise ValueError("Expected finite premultiplied float RGBA in [0,1], RGB ≤ alpha")
    _size([array.shape[1], array.shape[0]], "RGBA buffer")
    return array


def mask_alpha(mask):
    """Grayscale uint8 or finite float H×W alpha; no thresholding/feathering."""
    if isinstance(mask, Image.Image) and mask.mode != "L":
        raise ValueError("Mask must be an 8-bit grayscale L image")
    array = np.asarray(mask)
    if array.ndim != 2 or array.dtype.kind not in {"u", "f"}:
        raise ValueError("Mask must be a grayscale uint8 or float array")
    if array.dtype.kind == "u" and array.dtype != np.uint8:
        raise ValueError("Integer masks must be uint8")
    _size([array.shape[1], array.shape[0]], "mask buffer")
    output = array.astype(np.float32) / (255 if array.dtype == np.uint8 else 1)
    if not np.isfinite(output).all() or np.any(output < 0) or np.any(output > 1):
        raise ValueError("Mask alpha must be finite and in [0,1]")
    return output


def masked_layer(image, mask):
    """Multiply existing RGBA alpha by a same-size matte; preserve both inputs."""
    rgba, alpha = _premult(image), mask_alpha(mask)
    if rgba.shape[:2] != alpha.shape:
        raise ValueError("Image and mask dimensions differ")
    return (rgba * alpha[..., None]).astype(np.float32)


def over(background, foreground):
    """Porter–Duff foreground-over-background for equal-size premultiplied RGBA."""
    bg, fg = _premult(background), _premult(foreground)
    if bg.shape != fg.shape:
        raise ValueError("Compositing buffers must share the canvas dimensions")
    return np.clip(fg + bg * (1 - fg[..., 3:4]), 0, 1).astype(np.float32)


def straight_image(image):
    """Premultiplied float RGBA → straight RGBA PNG-ready PIL image."""
    array = _premult(image)
    rgb = np.divide(array[..., :3], array[..., 3:4],
                    out=np.zeros_like(array[..., :3]), where=array[..., 3:4] > 1e-8)
    return Image.fromarray(np.rint(np.clip(np.dstack([rgb, array[..., 3]]), 0, 1) * 255).astype(np.uint8))


def pose_at(motion, frame):
    """Evaluate normalized motion at an integer output frame; never extrapolate."""
    if type(frame) is not int or frame < 0:
        raise ValueError("Frame must be a nonnegative integer")
    if "pose" in motion:
        return copy.deepcopy(motion["pose"])
    keys = motion["keyframes"]
    if not keys[0]["frame"] <= frame <= keys[-1]["frame"]:
        raise ValueError("Requested frame is outside keyframe coverage")
    for key in keys:
        if frame == key["frame"]:
            return {k: copy.deepcopy(key[k]) for k in POSE_FIELDS}
    for left, right in zip(keys, keys[1:]):
        if left["frame"] < frame < right["frame"]:
            amount = (frame - left["frame"]) / (right["frame"] - left["frame"])
            if left["ease"] == "smoothstep":
                amount = amount * amount * (3 - 2 * amount)
            elif left["ease"] == "hold":
                amount = 0
            return {k: ([(1-amount)*a + amount*b for a, b in zip(left[k], right[k])]
                        if k in {"position", "scale"} else (1-amount)*left[k] + amount*right[k])
                    for k in POSE_FIELDS}
    raise ValueError("No keyframe span covers the requested frame")


def _inverse(pose, anchor):
    # Coordinates are pixel edges. Screen-positive rotation is clockwise.
    sx, sy = pose["scale"]
    theta = math.radians(pose["rotation"])
    cosine, sine = math.cos(theta), math.sin(theta)
    forward = np.array([[cosine*sx, -sine*sy], [sine*sx, cosine*sy]], np.float64)
    inverse = np.linalg.inv(forward)
    shift = np.asarray(anchor) - inverse @ np.asarray(pose["position"])
    return (*inverse[0], shift[0], *inverse[1], shift[1])


def _warp(array, canvas, pose, anchor, resample):
    canvas = tuple(_size(canvas, "canvas"))
    pose = _pose(pose)
    anchor = _pair(anchor, 0, max(array.shape[:2]), "anchor")
    if anchor[0] > array.shape[1] or anchor[1] > array.shape[0]:
        raise ValueError("Anchor is outside the source geometry")
    if not isinstance(resample, str) or resample not in RESAMPLING:
        raise ValueError("Unknown resampling mode")
    coefficients = _inverse(pose, anchor)
    def channel(values):
        plane = Image.fromarray(np.asarray(values, dtype=np.float32))
        return np.asarray(plane.transform(canvas, Image.Transform.AFFINE, coefficients,
                                         resample=RESAMPLING[resample], fillcolor=0), dtype=np.float32)
    if array.ndim == 2:
        return np.clip(channel(array), 0, 1)
    result = np.stack([channel(array[..., c]) for c in range(4)], axis=-1)
    # Bicubic can overshoot alpha above1. Clipping RGB/alpha independently
    # brightens nonsaturated colors. Recover the ratio before clipping coverage.
    alpha = result[..., 3:4]
    color = np.divide(result[..., :3], alpha, out=np.zeros_like(result[..., :3]),
                      where=alpha > 1e-8)
    alpha = np.clip(alpha, 0, 1)
    return np.concatenate([np.clip(color, 0, 1) * alpha, alpha], axis=-1)


def transform_layer(image, canvas, pose, anchor=(0, 0), resample="bilinear"):
    """Warp premultiplied RGB and alpha together; no hidden RGB edge bleed.

    Position locates the source anchor on the canvas. Outside-source pixels are
    transparent (never reflected/stretched). Opacity is applied after the warp.
    """
    pose = _pose(pose)
    return _warp(_premult(image), canvas, pose, anchor, resample) * pose["opacity"]


def transform_mask(mask, canvas, pose, anchor=(0, 0), resample="bilinear"):
    """Transform a matte identically to a color layer, preserving fractional alpha."""
    pose = _pose(pose)
    return _warp(mask_alpha(mask), canvas, pose, anchor, resample) * pose["opacity"]


def load_assets(spec, directory):
    """Hash/decode assets once. Masks are L; image files must be RGB/RGBA stills."""
    root, buffers = Path(directory).resolve(strict=True), {}
    for name, asset in spec["assets"].items():
        path = (root / asset["path"]).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Asset must be a file within the manifest directory")
        if path.stat().st_size > 100_000_000:
            raise ValueError("Asset exceeds the bounded image-file budget")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError(f"Asset SHA256 differs: {name}")
        with Image.open(io.BytesIO(data)) as image:
            if list(image.size) != asset["size"] or getattr(image, "n_frames", 1) != 1:
                raise ValueError(f"Asset dimensions/frame count differ: {name}")
            if image.getexif().get(274, 1) != 1:
                raise ValueError("Bake EXIF orientation into a new reviewed image before authoring masks")
            buffers[name] = mask_alpha(image) if asset["kind"] == "mask" else premultiply(image)
    return buffers


def render_frame(spec, buffers, frame):
    """Compose an output frame from validated config and already prepared buffers.

    `validate_config` and `load_assets` are the file boundary. For a production
    decoder, pass `premultiply(rgb)`/`mask_alpha(alpha)` buffers with identical
    declared geometry; no files are opened or hashes read by this function.
    `source_frame`/file identity validation remains the caller's responsibility
    for streamed replacement buffers. Source buffers are never modified.
    """
    _integer(frame, 0, spec["frame_count"] - 1, "frame")
    if set(buffers) != set(spec["assets"]):
        raise ValueError("Prepared buffers must exactly match the asset names")
    for name, asset in spec["assets"].items():
        shape = (*asset["size"][::-1], 4) if asset["kind"] == "image" else tuple(asset["size"][::-1])
        if np.asarray(buffers[name]).shape != shape:
            raise ValueError(f"Prepared buffer geometry differs: {name}")
    width, height = spec["canvas"]
    color = premultiply(np.array([[spec["background"]]], np.uint8))[0, 0]
    output = np.broadcast_to(color, (height, width, 4)).copy()
    for layer in spec["layers"]:
        if not layer["frames"][0] <= frame < layer["frames"][1]:
            continue
        source = buffers[layer["image"]]
        if "mask" in layer:
            source = masked_layer(source, buffers[layer["mask"]])
        foreground = transform_layer(source, spec["canvas"], pose_at(layer, frame),
                                     layer["anchor"], spec["resample"])
        if "reveal" in layer:
            reveal = layer["reveal"]
            pose = pose_at(reveal, frame)
            # Invert geometric coverage first; opacity then controls that reveal.
            alpha = transform_mask(buffers[reveal["mask"]], spec["canvas"],
                                   {**pose, "opacity": 1}, reveal["anchor"], spec["resample"])
            alpha = (1 - alpha if reveal["invert"] else alpha) * pose["opacity"]
            foreground = masked_layer(foreground, alpha)
        output = over(output, foreground)
    return straight_image(output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scene", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate and hash inputs; write nothing")
    parser.add_argument("--frames", help="Comma-separated output frame indices; default: every frame")
    parser.add_argument("--output-dir", type=Path, help="New directory for straight-alpha PNGs and receipt")
    args = parser.parse_args(argv)
    try:
        scene = args.scene.resolve(strict=True)
        raw = scene.read_bytes()
        spec = validate_config(json.loads(raw))
        buffers = load_assets(spec, scene.parent)
        frames = list(range(spec["frame_count"])) if args.frames is None else [int(v) for v in args.frames.split(",")]
        if not frames or len(set(frames)) != len(frames):
            raise ValueError("Frame selection must be nonempty with no duplicates")
        for frame in frames:
            _integer(frame, 0, spec["frame_count"] - 1, "selected frame")
        report = {"version": 1, "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                  "canvas": spec["canvas"], "fps": spec["fps"], "frame_count": spec["frame_count"],
                  "assets": spec["assets"], "alpha": "straight PNG; internal premultiplied encoded RGB",
                  "limits": "Authored mattes/clean plates required; no segmentation, tracking, hidden-pixel reconstruction or video/audio processing."}
        if args.check:
            print(json.dumps(report, indent=2))
            return 0
        if args.output_dir is None:
            raise ValueError("--output-dir is required for rendering")
        # A fresh directory protects every source and previous render; no overwrite option.
        args.output_dir.mkdir(parents=True, exist_ok=False)
        report["outputs"] = []
        for frame in frames:
            path = args.output_dir / f"frame-{frame:06d}.png"
            with path.open("xb") as stream:
                render_frame(spec, buffers, frame).save(stream, format="PNG")
            report["outputs"].append({"frame": frame, "seconds": frame/spec["fps"],
                                      "file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        with (args.output_dir / "receipt.json").open("x") as stream:
            json.dump(report, stream, indent=2)
            stream.write("\n")
        print(json.dumps({"frames": len(frames), "output_dir": str(args.output_dir)}))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(2, f"subject_layers: {error}\n")


if __name__ == "__main__":
    main()
