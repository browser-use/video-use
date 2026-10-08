"""Keyframed picture transforms and layered composition on an explicit frame clock."""

import math
from fractions import Fraction
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageChops, ImageColor, ImageDraw
from cards import mask_at, validate_mask
from edit_io import resolve, source_path

PARAMETERS = {"x", "y", "scale", "rotation", "opacity", "blur", "displacement"}


# interpolate a scalar property on the declared frame clock
def curve(value, frame):
    if isinstance(value, (int, float)):
        return float(value)
    points = value["points"]
    if frame <= points[0][0]:
        return float(points[0][1])
    if frame >= points[-1][0]:
        return float(points[-1][1])
    for (a, av), (b, bv) in zip(points, points[1:]):
        if a <= frame < b:
            t = (frame - a) / (b - a)
            easing = value.get("easing", "linear")
            if easing == "cubic_out":
                t = 1 - (1 - t) ** 3
            elif easing == "smoothstep":
                t = t * t * (3 - 2 * t)
            elif easing == "hold":
                t = 0
            return float(av + (bv - av) * t)
    raise ValueError("invalid curve")


# reject malformed keyframes and out of range property values
def validate_curve(value, label, low=-1e6, high=1e6):
    if isinstance(value, bool):
        raise ValueError(f"{label}: booleans are not effect values")
    if isinstance(value, (int, float)):
        values = [value]
    else:
        if not isinstance(value, dict) or set(value) - {"points", "easing"}:
            raise ValueError(f"{label}: expected value or keyframe curve")
        if value.get("easing", "linear") not in (
            "linear",
            "cubic_out",
            "smoothstep",
            "hold",
        ):
            raise ValueError("unsupported easing")
        points = value.get("points", [])
        if not points or any(not isinstance(p, list) or len(p) != 2 for p in points):
            raise ValueError("curve needs [frame,value] points")
        frames = [p[0] for p in points]
        if any(type(f) is not int or f < 0 for f in frames) or frames != sorted(
            set(frames)
        ):
            raise ValueError(
                "curve frames must be unique ascending nonnegative integers"
            )
        values = [p[1] for p in points]
    if any(
        isinstance(v, bool)
        or not isinstance(v, (int, float))
        or not math.isfinite(v)
        or not low <= v <= high
        for v in values
    ):
        raise ValueError(f"{label}: values must be finite in [{low},{high}]")


# validate supported transforms and shutter sampling
def validate_effects(spec):
    if not isinstance(spec, dict) or set(spec) - PARAMETERS - {"shutter", "samples"}:
        raise ValueError("unsupported picture effect")
    for key, value in spec.items():
        if key in ("samples", "shutter"):
            continue
        low, high = {
            "scale": (0.01, 100),
            "opacity": (0, 1),
            "blur": (0, 128),
            "displacement": (-0.5, 0.5),
        }.get(key, (-1e6, 1e6))
        validate_curve(value, key, low, high)
    samples = spec.get("samples", 1)
    shutter = spec.get("shutter", 0)
    if (
        type(samples) is not int
        or not 1 <= samples <= 16
        or isinstance(shutter, bool)
        or not isinstance(shutter, (int, float))
        or not math.isfinite(shutter)
        or not 0 <= shutter <= 2
    ):
        raise ValueError("invalid transform shutter sampling")


# require a forward source mapping over the entire output interval
def validate_time_map(points, count):
    if (
        not isinstance(points, list)
        or len(points) < (1 if count == 1 else 2)
        or any(not isinstance(p, list) or len(p) != 2 for p in points)
    ):
        raise ValueError("time_map needs output-frame/source-frame pairs")
    out = [p[0] for p in points]
    source = [p[1] for p in points]
    if (
        out[0] != 0
        or out[-1] != count - 1
        or any(type(f) is not int for f in out)
        or out != sorted(set(out))
    ):
        raise ValueError(
            "time_map must span output frames 0 through shot length minus one"
        )
    if any(
        isinstance(f, bool)
        or not isinstance(f, (int, float))
        or not math.isfinite(f)
        or f < 0
        for f in source
    ) or source != sorted(source):
        raise ValueError("time_map must have nondecreasing native source frames")


# validate the source window shared by shots and moving foreground layers
def validate_source_window(row):
    if row.get("source_frame") is not None and row.get("source_start") is not None:
        raise ValueError(
            "choose source_frame or source_start rather than two source origins"
        )
    native = row.get("source_frame")
    if native is not None and (type(native) is not int or native < 0):
        raise ValueError("source_frame must be a nonnegative native frame index")
    for key, default in (("speed", 1), ("source_start", 0)):
        value = row.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
        ):
            raise ValueError(f"{key} must be a finite number")
        if value < 0 or (key == "speed" and value == 0):
            raise ValueError(f"invalid {key}")
    if row.get("fit", "contain") != "contain":
        raise ValueError("only aspect-preserving contain is implemented")
    crop = row.get("source_crop")
    if crop is not None and (
        not isinstance(crop, list)
        or len(crop) != 4
        or any(type(v) is not int or v % 2 for v in crop)
        or min(crop[:2]) < 0
        or min(crop[2:]) <= 0
    ):
        raise ValueError("source_crop needs even [x,y,width,height] pixels")


# check layer sources geometry masks and active frame ranges
def validate_layers(manifest, root, check_files=True):
    ids = set()
    total = manifest["total_frames"]
    for layer in manifest.get("layers", []):
        allowed = {
            "id",
            "image",
            "source",
            "source_frame",
            "source_start",
            "source_crop",
            "speed",
            "start_frame",
            "end_frame",
            "mask",
            "clip",
            "fill",
            "fill_opacity",
            "effects",
            "reason",
        }
        if set(layer) - allowed:
            raise ValueError("unsupported layer fields")
        if not layer.get("id") or layer["id"] in ids:
            raise ValueError("layer ids must be unique")
        ids.add(layer["id"])
        a, b = layer["start_frame"], layer["end_frame"]
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= total:
            raise ValueError("layer is outside the picture clock")
        if ("image" in layer) + ("source" in layer) != 1:
            raise ValueError("layer needs exactly one image or source")
        if "image" in layer and set(layer) & {
            "source_frame",
            "source_start",
            "source_crop",
            "speed",
        }:
            raise ValueError("image layers cannot carry unused video source settings")
        if "source" in layer:
            validate_source_window(layer)
            source = manifest["sources"].get(layer["source"])
            if not source or source.get("study_only") or not source.get("provenance"):
                raise ValueError(
                    "layer needs an independently sourced render source with provenance"
                )
        if check_files:
            if "image" in layer and not resolve(root, layer["image"]).is_file():
                raise ValueError("layer image is missing")
            if "image" in layer:
                with Image.open(resolve(root, layer["image"])) as image:
                    if image.size != tuple(manifest["picture"][2:]):
                        raise ValueError(
                            "layer image must use picture-region dimensions"
                        )
            if "source" in layer:
                source_path(manifest, root, layer["source"])
        if "source" in layer and layer["source"] not in manifest["sources"]:
            raise ValueError("unknown layer source")
        if "fill" in layer:
            ImageColor.getrgb(layer["fill"])
        validate_curve(layer.get("fill_opacity", 0), "fill opacity", 0, 1)
        validate_effects(layer.get("effects", {}))
        if "mask" in layer:
            validate_mask(
                layer["mask"], a, b, tuple(manifest["picture"][2:]), root, check_files
            )
        if "clip" in layer:
            if len(layer["clip"]) != 4:
                raise ValueError("layer clip needs x,y,width,height curves")
            for i, v in enumerate(layer["clip"]):
                validate_curve(v, "clip", 0 if i > 1 else -100000, 100000)


# resample premultiplied rgba  optional shutter integrates transform motion
def transform(image, spec, frame):
    """Resample premultiplied RGBA; optional shutter integrates transform motion."""
    if not spec:
        return image
    width, height = image.size
    rgba = np.asarray(image.convert("RGBA"), np.float32) / 255
    rgba[:, :, :3] *= rgba[:, :, 3:4]
    samples = spec.get("samples", 1)
    shutter = spec.get("shutter", 0)
    result = np.zeros_like(rgba)
    for offset in (
        np.linspace(-shutter / 2, shutter / 2, samples) if samples > 1 else [0]
    ):
        t = frame + offset
        scale = curve(spec.get("scale", 1), t)
        angle = curve(spec.get("rotation", 0), t)
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, scale)
        matrix[:, 2] += [curve(spec.get("x", 0), t), curve(spec.get("y", 0), t)]
        sample = cv2.warpAffine(
            rgba,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
        )
        displacement = curve(spec.get("displacement", 0), t)
        if displacement:
            yy, xx = np.indices((height, width), np.float32)
            xx += np.sin(yy / height * math.pi * 2) * displacement * width
            sample = cv2.remap(
                sample, xx, yy, cv2.INTER_CUBIC, borderMode=cv2.BORDER_CONSTANT
            )
        blur = curve(spec.get("blur", 0), t)
        if blur:
            sample = cv2.GaussianBlur(sample, (0, 0), blur)
        result += sample / samples
    result[:, :, 3] *= curve(spec.get("opacity", 1), frame)
    # RGB must receive the same opacity before unpremultiplication.
    result[:, :, :3] *= curve(spec.get("opacity", 1), frame)
    alpha = result[:, :, 3:4]
    result[:, :, :3] = np.divide(
        result[:, :, :3], alpha, out=np.zeros_like(result[:, :, :3]), where=alpha > 1e-6
    )
    return Image.fromarray(
        np.round(np.clip(result, 0, 1) * 255).astype(np.uint8), "RGBA"
    )


# array order is back to front  foreground movies are staged by the renderer
class LayerCompositor:
    """Array order is back to front; foreground movies are staged by the renderer."""

    # open the supplied static images and staged foreground movies
    def __init__(self, manifest, root, staged):
        self.manifest = manifest
        self.root = root
        self.staged = staged
        self.readers = {}
        self.images = {}
        self.size = tuple(manifest["picture"][2:])
        for layer in manifest.get("layers", []):
            if "image" in layer:
                with Image.open(resolve(root, layer["image"])) as im:
                    self.images[layer["id"]] = im.convert("RGBA")
                if self.images[layer["id"]].size != self.size:
                    raise ValueError("layer images must use picture-region dimensions")
            else:
                self.readers[layer["id"]] = cv2.VideoCapture(str(staged[layer["id"]]))

    # compose active layers over the current picture in declared order
    def frame(self, base, n):
        result = base.convert("RGBA")
        for layer in self.manifest.get("layers", []):
            if not layer["start_frame"] <= n < layer["end_frame"]:
                continue
            if "image" in layer:
                im = self.images[layer["id"]].copy()
            else:
                ok, bgr = self.readers[layer["id"]].read()
                if not ok:
                    raise ValueError(f'layer {layer["id"]} ended early')
                im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA))
            if layer.get("mask"):
                im.putalpha(
                    ImageChops.multiply(
                        im.getchannel("A"),
                        mask_at(layer["mask"], n, self.size, self.root),
                    )
                )
            if layer.get("clip"):
                x, y, w, h = [round(curve(v, n)) for v in layer["clip"]]
                mask = Image.new("L", self.size)
                if w > 0 and h > 0:
                    ImageDraw.Draw(mask).rectangle(
                        (x, y, x + w - 1, y + h - 1), fill=255
                    )
                im.putalpha(ImageChops.multiply(im.getchannel("A"), mask))
            if layer.get("fill"):
                filled = Image.new(
                    "RGBA", self.size, ImageColor.getrgb(layer["fill"]) + (255,)
                )
                filled.putalpha(im.getchannel("A"))
                im = Image.blend(im, filled, curve(layer.get("fill_opacity", 1), n))
            result.alpha_composite(transform(im, layer.get("effects", {}), n))
        return result

    # release foreground movie readers
    def close(self):
        for reader in self.readers.values():
            reader.release()


# preserve existing media and diagnostic logs before starting an encoder
def check_output(path, log):
    for target in (path, log):
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"output already exists: {target}")
    path.parent.mkdir(parents=True, exist_ok=True)


# use the declared positive frame rate for intermediate picture output
def output_fps(manifest):
    try:
        fps = Fraction(str(manifest.get("fps", 30)))
    except (ValueError, ZeroDivisionError) as error:
        raise ValueError("fps must be a positive frame rate") from error
    if fps <= 0:
        raise ValueError("fps must be a positive frame rate")
    return fps


# encode composed picture frames at the declared frame rate
def render_layers(manifest, root, picture, staged, out):
    import subprocess

    out = Path(out)
    check_output(out, Path(str(out) + ".log"))
    fps = output_fps(manifest)
    from edit_clock import check_partition

    check_partition(manifest.get("shots", []), manifest["total_frames"])
    compositor = LayerCompositor(manifest, root, staged)
    reader = cv2.VideoCapture(str(picture))
    index = 0
    width, height = manifest["picture"][2:]
    with Path(str(out) + ".log").open("wb") as log:
        writer = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-n",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "ffv1",
                "-pix_fmt",
                "bgr0",
                str(out),
            ],
            stdin=subprocess.PIPE,
            stderr=log,
        )
        try:
            for n in range(manifest["total_frames"]):
                ok, bgr = reader.read()
                if not ok:
                    raise ValueError("base picture ended early")
                while n >= manifest["shots"][index]["end_frame"]:
                    index += 1
                im = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGBA))
                im = transform(im, manifest["shots"][index].get("effects", {}), n)
                composed = compositor.frame(im, n)
                background = Image.new("RGBA", im.size, "black")
                background.alpha_composite(composed)
                writer.stdin.write(background.convert("RGB").tobytes())
            writer.stdin.close()
            if writer.wait() != 0:
                raise RuntimeError("effect encoding failed")
        except BaseException:
            writer.kill()
            writer.wait()
            raise
        finally:
            reader.release()
            compositor.close()


# native-frame retiming with explicit nearest-frame sampling and no invented flow
def stage_mapped(manifest, root, shot, path):
    """Native-frame retiming with explicit nearest-frame sampling and no invented flow."""
    import subprocess
    from PIL import ImageOps

    path = Path(path)
    check_output(path, path.with_suffix(".log"))
    fps = output_fps(manifest)
    count = shot["end_frame"] - shot["start_frame"]
    width, height = manifest["picture"][2:]
    validate_time_map(shot["time_map"], count)
    validate_source_window(shot)
    if any(key in shot for key in ("source_start", "source_frame", "speed")):
        raise ValueError("time_map uses absolute native frames and cannot combine with source origin or speed")
    points = np.asarray(shot["time_map"])
    targets = np.floor(
        np.interp(np.arange(count), points[:, 0], points[:, 1]) + 0.5
    ).astype(int)
    reader = cv2.VideoCapture(str(source_path(manifest, root, shot["source"])))
    index = -1
    image = None
    with path.with_suffix(".log").open("wb") as log:
        writer = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-n",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{width}x{height}",
                "-r",
                str(fps),
                "-i",
                "pipe:0",
                "-an",
                "-c:v",
                "libx264",
                "-crf",
                "0",
                "-preset",
                "fast",
                "-threads",
                "2",
                "-pix_fmt",
                "yuv420p",
                "-video_track_timescale",
                str(fps.numerator * 512),
                "-map_metadata",
                "-1",
                str(path),
            ],
            stdin=subprocess.PIPE,
            stderr=log,
        )
        try:
            for target in targets:
                while index < target:
                    ok, bgr = reader.read()
                    if not ok:
                        raise ValueError("time_map exceeds native source frames")
                    index += 1
                    image = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
                cropped = image
                if shot.get("source_crop"):
                    x, y, w, h = shot["source_crop"]
                    if x + w > image.width or y + h > image.height:
                        raise ValueError("source_crop extends outside source dimensions")
                    cropped = image.crop((x, y, x + w, y + h))
                writer.stdin.write(
                    ImageOps.pad(
                        cropped,
                        (width, height),
                        method=Image.Resampling.LANCZOS,
                        color="black",
                    ).tobytes()
                )
            writer.stdin.close()
            if writer.wait() != 0:
                raise RuntimeError("retimed picture encoding failed")
        except BaseException:
            writer.kill()
            writer.wait()
            raise
        finally:
            reader.release()
