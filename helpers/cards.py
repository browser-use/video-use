"""Frame-addressed caption cards: measured ink, curves, settles and subject exclusion."""

import argparse
import math
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import (
    Image,
    ImageDraw,
    ImageFont,
    ImageFilter,
    ImageColor,
    ImageChops,
    features,
)

from edit_io import load_json, resolve, save_json


# load the declared font layout without silently changing text shaping
@lru_cache(maxsize=32)
def font_at(path, size, layout="basic"):
    if layout not in ("basic", "raqm"):
        raise ValueError("font_layout must be basic or raqm")
    if layout == "raqm" and not features.check_feature("raqm"):
        raise RuntimeError(
            "RAQM font layout is required by this manifest but unavailable; use the container environment"
        )
    return ImageFont.truetype(
        path,
        size,
        layout_engine=ImageFont.Layout.RAQM
        if layout == "raqm"
        else ImageFont.Layout.BASIC,
    )


# resolve project fonts or bundled licensed font assets
def font_path(manifest, root, name):
    value = manifest["fonts"][name]
    assets = Path(__file__).resolve().parents[1] / "assets"
    if value.startswith("@assets/"):
        return str((assets / value[len("@assets/"):]).resolve())
    if value.startswith("@skill/assets/fonts/"):
        return str((assets / "fonts" / value[len("@skill/assets/fonts/"):]).resolve())
    return str(resolve(root, value))


# crop text to its actual nontransparent glyph pixels
def text_mask(text, font):
    box = font.getbbox(text)
    im = Image.new("L", (max(1, box[2] - box[0] + 12), max(1, box[3] - box[1] + 12)))
    ImageDraw.Draw(im).text((6 - box[0], 6 - box[1]), text, font=font, fill=255)
    ink = im.getbbox()
    if ink is None:
        raise ValueError("caption has no visible glyphs")
    return im.crop(ink)


# place glyphs along a curve using the local tangent
def curve_mask(
    text, font, bend_ratio=0, kind="parabola", angle_degrees=140.4, direction="down"
):
    """Place glyphs on a parabola or circle, rotated to the local tangent."""
    if kind not in ("parabola", "circle"):
        raise ValueError("unsupported curve kind")
    if direction not in ("down", "up") or not 0 < angle_degrees < 180:
        raise ValueError("invalid circular arc")
    advances = [float(font.getlength(text[:i])) for i in range(len(text) + 1)]
    width = max(1, advances[-1])
    bend = width * bend_ratio
    angle = math.radians(angle_degrees)
    radius = width / angle
    pad = font.size * 2
    out = Image.new(
        "L",
        (
            math.ceil(width + pad * 2),
            math.ceil(max(abs(bend), radius * 2) + pad * 2 + font.size * 2),
        ),
    )
    for i, ch in enumerate(text):
        if ch.isspace():
            continue
        center = (advances[i] + advances[i + 1]) / 2
        u = center / width
        y = bend * (2 * u - 1) ** 2
        slope = bend * 4 * (2 * u - 1) / width
        xx = center
        if kind == "circle":
            theta = (u - 0.5) * angle
            sign = 1 if direction == "down" else -1
            xx = width / 2 + radius * math.sin(theta)
            y = radius + sign * radius * math.cos(theta)
            slope = -sign * math.tan(theta)
        glyph = text_mask(ch, font).rotate(
            -math.degrees(math.atan(slope)),
            resample=Image.Resampling.BICUBIC,
            expand=True,
        )
        x = round(pad + xx - glyph.width / 2)
        yy = round(pad + y - min(0, bend) - glyph.height / 2)
        patch = out.crop((x, yy, x + glyph.width, yy + glyph.height))
        out.paste(ImageChops.lighter(patch, glyph), (x, yy))
    box = out.getbbox()
    if box is None:
        raise ValueError("caption has no visible glyphs")
    return out.crop(box)


# size and color a measured line of text
def line_sprite(line, manifest, root):
    font = font_at(
        font_path(manifest, root, line["font"]),
        240,
        manifest.get("font_layout", "basic"),
    )
    if line.get("curve"):
        curve = line["curve"]
        mask = curve_mask(
            line["text"],
            font,
            float(curve.get("bend_ratio", 0)),
            curve.get("kind", "parabola"),
            float(curve.get("angle_degrees", 140.4)),
            curve.get("direction", "down"),
        )
    else:
        mask = text_mask(line["text"], font)
    cap = font.getbbox("H")
    factor = float(line["cap_height"]) / (cap[3] - cap[1])
    width = max(1, round(mask.width * factor))
    height = max(1, round(mask.height * factor))
    if line.get("fit") == "ink_box":
        width, height = map(int, line["ink_size"])
    elif line.get("max_width") and width > line["max_width"]:
        factor = line["max_width"] / width
        width = round(width * factor)
        height = max(1, round(height * factor))
    if width < 1 or height < 1:
        raise ValueError("invalid caption dimensions")
    mask = mask.resize((width, height), Image.Resampling.LANCZOS)
    color = ImageColor.getrgb(line.get("color", "#ffffff"))
    if len(color) != 3:
        raise ValueError("card colors must use RGB values without alpha")
    layer = Image.new("RGBA", mask.size, (*color, 255))
    if line.get("gradient"):
        end = np.array(ImageColor.getrgb(line["gradient"]["to"]), float)
        if len(end) != 3:
            raise ValueError("card gradient colors must use RGB values without alpha")
        first = np.array(color, float)
        rgb = np.round(
            first[None, :] + (end - first)[None, :] * np.linspace(0, 1, height)[:, None]
        ).astype("uint8")
        layer = Image.fromarray(
            np.repeat(rgb[:, None, :], width, axis=1), "RGB"
        ).convert("RGBA")
    layer.putalpha(mask)
    return layer


# read a supplied subject mask for the requested frame
def mask_at(spec, frame, size, root):
    """Read a supplied/tracked matte; this helper does not invent segmentation."""
    if "file" in spec:
        image = Image.open(resolve(root, spec["file"])).convert("L")
    elif "pattern" in spec:
        index = frame - spec.get("start_frame", 0) + spec.get("first_index", 0)
        image = Image.open(resolve(root, spec["pattern"].format(frame=index))).convert(
            "L"
        )
    elif "polygons" in spec:
        data = load_json(resolve(root, spec["polygons"]))
        index = frame - spec.get("start_frame", 0)
        rows = data["frames"]
        row = next((r for r in rows if r["frame"] == index), None)
        if row is None:
            raise ValueError(f"missing matte frame {index}")
        if row.get("needs_review"):
            raise ValueError(f"matte frame {index} needs correction")
        image = Image.new("L", size)
        ImageDraw.Draw(image).polygon([tuple(p) for p in row["polygon"]], fill=255)
    else:
        raise ValueError("matte requires file, pattern, or polygons")
    if image.size != size:
        raise ValueError(f"matte size {image.size} differs from picture {size}")
    feather = float(spec.get("feather", 0))
    if feather < 0:
        raise ValueError("negative matte feather")
    return image.filter(ImageFilter.GaussianBlur(feather)) if feather else image


# validate mask settings and enumerate their required input paths
def mask_paths(spec, start, end, root):
    if not isinstance(spec, dict) or set(spec) - {
        "file",
        "pattern",
        "polygons",
        "start_frame",
        "first_index",
        "feather",
    }:
        raise ValueError("unsupported matte fields")
    kinds = set(spec) & {"file", "pattern", "polygons"}
    if len(kinds) != 1:
        raise ValueError("matte needs exactly one file pattern or polygon sequence")
    for key in ("start_frame", "first_index"):
        if type(spec.get(key, 0)) is not int:
            raise ValueError("matte offsets must be integer frames")
    feather = spec.get("feather", 0)
    if (
        isinstance(feather, bool)
        or not isinstance(feather, (int, float))
        or not math.isfinite(feather)
        or not 0 <= feather <= 128
    ):
        raise ValueError("invalid matte feather")
    if "pattern" in spec:
        return {
            resolve(
                root,
                spec["pattern"].format(
                    frame=n - spec.get("start_frame", 0) + spec.get("first_index", 0)
                ),
            )
            for n in range(start, end)
        }
    return {resolve(root, spec[next(iter(kinds))])}


# check all declared mask frames and their review status
def validate_mask(spec, start, end, size, root, check_files=True):
    paths = mask_paths(spec, start, end, root)
    if not check_files:
        return
    for path in paths:
        if not path.is_file():
            raise ValueError(f"missing matte input {path}")
        if "polygons" not in spec:
            with Image.open(path) as image:
                if image.size != size:
                    raise ValueError("matte must use picture-region dimensions")
    if "polygons" in spec:
        data = load_json(next(iter(paths)))
        rows = {r["frame"]: r for r in data["frames"]}
        for n in range(start, end):
            row = rows.get(n - spec.get("start_frame", 0))
            if row is None or row.get("needs_review"):
                raise ValueError("matte frame is missing or needs correction")


# render frame addressed text cards and report their visible bounds
class CardRenderer:
    # prepare measured text sprites and validate the output clock
    def __init__(self, manifest, root):
        self.manifest = manifest
        self.root = Path(root)
        self.fps = Fraction(str(manifest.get("fps", 30)))
        if (
            self.fps <= 0
            or type(manifest.get("total_frames")) is not int
            or manifest["total_frames"] <= 0
        ):
            raise ValueError(
                "cards need a positive frame rate and positive integer frame count"
            )
        self.size = tuple(manifest["picture"][2:])
        if len(self.size) != 2 or any(
            type(value) is not int or value <= 0 for value in self.size
        ):
            raise ValueError("picture needs positive integer width and height")
        self.sprites = {}
        for card in manifest.get("cards", []):
            if (
                any(
                    type(card.get(key)) is not int
                    for key in ("start_frame", "end_frame")
                )
                or not 0
                <= card["start_frame"]
                < card["end_frame"]
                <= manifest["total_frames"]
            ):
                raise ValueError("card interval must fit the output timeline")
            animation = card.get("animation", {})
            duration = animation.get("entry_frames", 0)
            if type(duration) is not int or duration < 0:
                raise ValueError("entry_frames must be a nonnegative integer")
            for key, default in (("power", 0.6), ("scale_from", 1), ("blur_from", 0), ("blur_to", 0)):
                value = float(animation.get(key, default))
                if not math.isfinite(value) or value < 0 or (key in ("power", "scale_from") and value == 0):
                    raise ValueError("invalid caption animation")
            if card.get("occlusion_mask"):
                validate_mask(card["occlusion_mask"], card["start_frame"], card["end_frame"], self.size, self.root)
            if card["id"] in self.sprites:
                raise ValueError("duplicate caption id")
            self.sprites[card["id"]] = [
                line_sprite(l, manifest, self.root) for l in card["lines"]
            ]

    # compose active cards and apply their authored animation and subject masks
    def frame(self, n, measure=False):
        if type(n) is not int or not 0 <= n < self.manifest["total_frames"]:
            raise ValueError("requested frame is outside the output timeline")
        canvas = Image.new("RGBA", self.size)
        boxes = []
        for card in self.manifest.get("cards", []):
            if not card["start_frame"] <= n < card["end_frame"]:
                continue
            first_box = len(boxes)
            animation = card.get("animation", {})
            duration = animation.get("entry_frames", 0)
            progress = (
                min(1, (n - card["start_frame"]) / max(1, duration)) if duration else 1
            )
            easing = animation.get("easing", "cubic_out")
            if easing not in ("cubic_out", "power"):
                raise ValueError("unsupported caption easing")
            power = float(animation.get("power", 0.6))
            if power <= 0:
                raise ValueError("animation power must be positive")
            eased = progress**power if easing == "power" else 1 - (1 - progress) ** 3
            scale = 1 + (float(animation.get("scale_from", 1)) - 1) * (1 - eased)
            blur_to = float(animation.get("blur_to", 0))
            blur = blur_to + (float(animation.get("blur_from", blur_to)) - blur_to) * (
                1 - eased
            )
            if scale <= 0 or blur < 0:
                raise ValueError("invalid caption animation")
            card_layer = Image.new("RGBA", self.size)
            for line, original in zip(card["lines"], self.sprites[card["id"]]):
                sprite = original
                if scale != 1:
                    sprite = original.resize(
                        (
                            max(1, round(original.width * scale)),
                            max(1, round(original.height * scale)),
                        ),
                        Image.Resampling.LANCZOS,
                    )
                x = round(line["x"] + (original.width - sprite.width) / 2)
                y = round(line["y"] + (original.height - sprite.height) / 2)
                if (
                    min(x, y) < 0
                    or x + sprite.width > self.size[0]
                    or y + sprite.height > self.size[1]
                ):
                    raise ValueError(f'caption {card["id"]} clips at frame {n}')
                card_layer.alpha_composite(sprite, (x, y))
                boxes.append(
                    {
                        "card": card["id"],
                        "text": line["text"],
                        "box": [x, y, sprite.width, sprite.height],
                    }
                )
            if blur:
                card_layer = card_layer.filter(ImageFilter.GaussianBlur(blur))
            if card.get("outline") or card.get("shadow"):
                original = card_layer
                support = Image.new("RGBA", self.size)
                for kind in ("shadow", "outline"):
                    spec = card.get(kind)
                    if not spec:
                        continue
                    unknown = set(spec) - {"color", "radius", "opacity", "offset"}
                    if unknown:
                        raise ValueError(
                            f"unsupported {kind} fields: {sorted(unknown)}"
                        )
                    radius = float(spec.get("radius", 1))
                    opacity = float(spec.get("opacity", 1))
                    if (
                        not math.isfinite(radius)
                        or not 0 <= radius <= 64
                        or not 0 <= opacity <= 1
                    ):
                        raise ValueError("invalid caption support")
                    alpha = original.getchannel("A")
                    alpha = (
                        alpha.filter(ImageFilter.MaxFilter(2 * math.ceil(radius) + 1))
                        if kind == "outline"
                        else alpha.filter(ImageFilter.GaussianBlur(radius))
                    )
                    alpha = alpha.point(lambda v: round(v * opacity))
                    dx, dy = spec.get("offset", [0, 0])
                    shift = Image.new("L", self.size)
                    shift.paste(alpha, (round(dx), round(dy)))
                    layer = Image.new(
                        "RGBA",
                        self.size,
                        ImageColor.getrgb(spec.get("color", "#000000")) + (255,),
                    )
                    layer.putalpha(shift)
                    support.alpha_composite(layer)
                support.alpha_composite(original)
                card_layer = support
            support_box = card_layer.getchannel("A").getbbox()
            if card.get("occlusion_mask"):
                mask = (
                    np.asarray(
                        mask_at(card["occlusion_mask"], n, self.size, self.root),
                        np.float32,
                    )
                    / 255
                )
                alpha = np.asarray(card_layer.getchannel("A"), np.float32) * (1 - mask)
                card_layer.putalpha(Image.fromarray(np.round(alpha).astype("uint8")))
            visible_box = card_layer.getchannel("A").getbbox()
            for row in boxes[first_box:]:
                row["card_support_xyxy"] = list(support_box) if support_box else None
                row["visible_support_xyxy"] = list(visible_box) if visible_box else None
            canvas.alpha_composite(card_layer)
        return (canvas, boxes) if measure else canvas

    # stage the movie so encoder failures leave no final output
    def write_movie(self, path):
        import os
        import tempfile

        path = Path(path)
        log_path = path.with_suffix(".log")
        if path == log_path:
            raise ValueError("movie path collides with encoder log")
        for output in (path, log_path):
            if output.exists() or output.is_symlink():
                raise FileExistsError("choose new caption movie and log paths")
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".cards-", dir=path.parent) as work:
            staged = Path(work) / path.name
            self._write_movie(staged)
            os.link(staged, path)
            try:
                os.link(staged.with_suffix(".log"), log_path)
            except BaseException:
                if path.exists() and path.samefile(staged):
                    path.unlink()
                raise

    # encode transparent frames without replacing an existing output
    def _write_movie(self, path):
        import subprocess

        path = Path(path)
        if path.exists() or path.is_symlink():
            raise FileExistsError("choose a new caption movie path")
        path.parent.mkdir(parents=True, exist_ok=True)
        width, height = self.size
        log_path = path.parent / (path.stem + ".log")
        if log_path.exists() or log_path.is_symlink():
            raise FileExistsError("choose a new caption log path")
        if path == log_path:
            raise ValueError("movie path collides with encoder log")
        with log_path.open("xb") as log:
            proc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-n",
                    "-threads",
                    "2",
                    "-f",
                    "rawvideo",
                    "-pixel_format",
                    "rgba",
                    "-video_size",
                    f"{width}x{height}",
                    "-framerate",
                    str(self.fps),
                    "-i",
                    "pipe:0",
                    "-an",
                    "-c:v",
                    "qtrle",
                    "-pix_fmt",
                    "argb",
                    str(path),
                ],
                stdin=subprocess.PIPE,
                stderr=log,
            )
            try:
                for n in range(self.manifest["total_frames"]):
                    proc.stdin.write(self.frame(n).tobytes())
                proc.stdin.close()
                if proc.wait() != 0:
                    raise RuntimeError("caption movie encoding failed; inspect its log")
            except BaseException:
                proc.kill()
                proc.wait()
                raise


# write a measured still or transparent movie from the card manifest
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest")
    p.add_argument("--frame", type=int)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    m = load_json(a.manifest)
    renderer = CardRenderer(m, Path(a.manifest).resolve().parent)
    output = Path(a.out)
    report = Path(str(output) + ".json")
    if (
        output.exists()
        or output.is_symlink()
        or (a.frame is not None and (report.exists() or report.is_symlink()))
    ):
        p.error("choose a new caption output path")
    output.parent.mkdir(parents=True, exist_ok=True)
    if a.frame is not None:
        image, boxes = renderer.frame(a.frame, True)
        image.save(a.out)
        save_json(str(a.out) + ".json", boxes)
    else:
        renderer.write_movie(a.out)


if __name__ == "__main__":
    main()
