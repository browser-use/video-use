"""Generate source-bound binary subject masks with an optional local SAM 2 backend.

The input is one explicitly selected, continuous shot, represented by hashed
frames and authored point/box prompts. This does not download models, identify
people, repair edges, reconstruct hidden pixels, or certify a correct track.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import hashlib
import importlib.metadata
import importlib.util
import json
import math
from pathlib import Path
import re
import tempfile

import numpy as np
from PIL import Image


SHA = re.compile(r"[a-f0-9]{64}")
CONFIGS = tuple(f"configs/sam2.1/sam2.1_hiera_{size}.yaml" for size in ("t", "s", "b+", "l"))
# Keep official decoder/memory safeguards without enabling the builder's hole fill.
SAM2_HYDRA_OVERRIDES = (
    "++model.fill_hole_area=0",
    "++model.sam_mask_decoder_extra_args.dynamic_multimask_via_stability=true",
    "++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_delta=0.05",
    "++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_thresh=0.98",
    "++model.binarize_mask_from_pts_for_mem_enc=true",
)
MASK_LOGIT_THRESHOLD = 0.0


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer in [{low}, {high}]")
    return value


def finite(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be finite in [{low}, {high}]")
    return float(value)


def fields(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise ValueError(f"Invalid {label} fields")


def checksum(value, label):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase SHA256")


def validate_plan(plan):
    """Validate geometry and authored clocks without loading a model or files."""
    fields(plan, ("version", "source_sha256", "width", "height", "frames", "objects", "prompts"),
           ("version", "source_sha256", "width", "height", "frames", "objects", "prompts"), "plan")
    integer(plan["version"], "version", 1, 1)
    checksum(plan["source_sha256"], "source_sha256")
    width = integer(plan["width"], "width", 16, 4096)
    height = integer(plan["height"], "height", 16, 4096)
    frames = plan["frames"]
    if not isinstance(frames, list) or not 1 <= len(frames) <= 600:
        raise ValueError("Select 1 through 600 frames from one continuous shot")
    if width * height > 8_388_608 or width * height * len(frames) > 1_200_000_000:
        raise ValueError("Selected frames exceed the pixel budget")
    last_frame, last_time, names = -1, -1., set()
    for frame in frames:
        fields(frame, ("file", "sha256", "source_frame", "source_time"),
               ("file", "sha256", "source_frame", "source_time"), "frame")
        name = frame["file"]
        if not isinstance(name, str) or not name or "\\" in name:
            raise ValueError("Frame file must be a relative PNG or JPEG path")
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or path.suffix.lower() not in (".png", ".jpg", ".jpeg") or name in names:
            raise ValueError("Frame files must be unique relative PNG or JPEG paths")
        names.add(name)
        checksum(frame["sha256"], "frame sha256")
        n = integer(frame["source_frame"], "source_frame", 0, 10_000_000)
        t = finite(frame["source_time"], "source_time", 0, 86400)
        if n <= last_frame or t <= last_time:
            raise ValueError("Source frame indices and presentation times must strictly increase")
        last_frame, last_time = n, t
    if frames[-1]["source_time"] - frames[0]["source_time"] > 120:
        raise ValueError("A selected shot must span at most 120 seconds")
    objects = plan["objects"]
    if not isinstance(objects, list) or not 1 <= len(objects) <= 8:
        raise ValueError("Select 1 through 8 explicitly prompted objects")
    ids = set()
    for obj in objects:
        fields(obj, ("id", "intervals"), ("id", "intervals"), "object")
        ident = integer(obj["id"], "object id", 1, 2**31 - 1)
        if ident in ids:
            raise ValueError("Object IDs must be unique")
        ids.add(ident)
        intervals = obj["intervals"]
        if not isinstance(intervals, list) or not 1 <= len(intervals) <= 32:
            raise ValueError("Each object needs explicit half-open active frame intervals")
        previous = 0
        for interval in intervals:
            if not isinstance(interval, list) or len(interval) != 2:
                raise ValueError("Each interval must be [first, stop]")
            start = integer(interval[0], "interval first", 0, len(frames) - 1)
            end = integer(interval[1], "interval stop", 1, len(frames))
            if start < previous or start >= end:
                raise ValueError("Active intervals must be ordered, nonoverlapping and nonempty")
            previous = end
    prompts = plan["prompts"]
    if not isinstance(prompts, list) or not 1 <= len(prompts) <= 256:
        raise ValueError("Provide 1 through 256 authored prompts")
    initialized, seen = set(), set()
    for prompt in prompts:
        fields(prompt, ("frame", "object_id", "points", "labels", "box", "empty"), ("frame", "object_id"), "prompt")
        n = integer(prompt["frame"], "prompt frame", 0, len(frames) - 1)
        ident = integer(prompt["object_id"], "prompt object_id", 1, 2**31 - 1)
        if ident not in ids or (n, ident) in seen:
            raise ValueError("Prompts need a known object and one combined prompt per object/frame")
        seen.add((n, ident))
        if "empty" in prompt:
            if prompt["empty"] is not True or set(prompt) != {"frame", "object_id", "empty"} or n == 0:
                raise ValueError("An explicit empty mask is a later correction with no points/box")
            continue
        if ("points" in prompt) != ("labels" in prompt):
            raise ValueError("Point coordinates and labels must be supplied together")
        positive = False
        if "points" in prompt:
            points, labels = prompt["points"], prompt["labels"]
            if not isinstance(points, list) or not isinstance(labels, list) or not 1 <= len(points) <= 64 or len(points) != len(labels):
                raise ValueError("Provide 1 through 64 point/label pairs")
            for point, label in zip(points, labels):
                if not isinstance(point, list) or len(point) != 2:
                    raise ValueError("Points must be [x,y] pixel centers")
                finite(point[0], "point x", 0, width - 1)
                finite(point[1], "point y", 0, height - 1)
                integer(label, "point label", 0, 1)
            positive = 1 in labels
        if "box" in prompt:
            box = prompt["box"]
            if not isinstance(box, list) or len(box) != 4:
                raise ValueError("Box must be [left,top,right,bottom] pixel centers")
            x0, y0, x1, y1 = [finite(v, "box coordinate", 0, max(width, height)) for v in box]
            if not 0 <= x0 < x1 <= width - 1 or not 0 <= y0 < y1 <= height - 1:
                raise ValueError("Box must have positive area inside the stored frame")
            positive = True
        if "points" not in prompt and "box" not in prompt:
            raise ValueError("A prompt needs points, a box, or an explicit later empty mask")
        if not positive:
            raise ValueError("Each nonempty keyframe seed needs a positive point or box; negative-only tracked corrections are unsupported")
        if n == 0 and positive:
            initialized.add(ident)
    if initialized != ids:
        raise ValueError("Every object needs a positive point or box seed on selected frame 0; split arriving subjects into a new shot")
    return json.loads(json.dumps(plan, allow_nan=False))


def load_plan(path):
    """Bind every source frame to its bytes and stored, unrotated geometry."""
    path = Path(path).resolve()
    plan = validate_plan(json.loads(path.read_text()))
    root = path.parent
    for frame in plan["frames"]:
        file = root / frame["file"]
        if file.is_symlink() or not file.resolve().is_relative_to(root) or not file.is_file():
            raise ValueError("Source frame is missing or leaves the plan directory")
        if file.stat().st_size > 100_000_000 or digest(file) != frame["sha256"]:
            raise ValueError("Source frame byte identity mismatch")
        with Image.open(file) as image:
            if image.size != (plan["width"], plan["height"]) or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Source frame geometry/animation differs from the plan")
            if image.getexif().get(274, 1) != 1:
                raise ValueError("Normalize EXIF orientation before authoring prompts")
            image.load()
    return plan, root


def sam2_masks(plan, frame_directory, checkpoint, model_config, device):
    """Yield (selected frame index, object ID, binary uint8 mask) offline."""
    import torch
    from sam2.build_sam import build_sam2_video_predictor

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable; install the optional backend separately")
    predictor = build_sam2_video_predictor(
        model_config, str(checkpoint), device=device, apply_postprocessing=False,
        hydra_overrides_extra=list(SAM2_HYDRA_OVERRIDES))
    mixed = torch.autocast("cuda", dtype=torch.bfloat16) if device == "cuda" else nullcontext()
    with torch.inference_mode(), mixed:
        state = predictor.init_state(video_path=str(frame_directory), offload_video_to_cpu=True, offload_state_to_cpu=True)
        for prompt in sorted(plan["prompts"], key=lambda p: (p["frame"], p["object_id"])):
            common = dict(inference_state=state, frame_idx=prompt["frame"], obj_id=prompt["object_id"])
            if prompt.get("empty"):
                predictor.add_new_mask(**common, mask=np.zeros((plan["height"], plan["width"]), dtype=bool))
            else:
                options = {}
                if "points" in prompt:
                    options.update(points=np.asarray(prompt["points"], dtype=np.float32), labels=np.asarray(prompt["labels"], dtype=np.int32))
                if "box" in prompt:
                    options["box"] = np.asarray(prompt["box"], dtype=np.float32)
                predictor.add_new_points_or_box(**common, **options)
        for frame, ids, logits in predictor.propagate_in_video(state, start_frame_idx=0):
            if not torch.isfinite(logits).all().item():
                raise RuntimeError("SAM2 returned nonfinite mask logits")
            for index, ident in enumerate(ids):
                yield int(frame), int(ident), ((logits[index, 0] > MASK_LOGIT_THRESHOLD).cpu().numpy().astype(np.uint8) * 255)


def run(plan_path, output, *, checkpoint, checkpoint_sha256, model_config=CONFIGS[-1], device="cuda", backend=None):
    """Write a complete mask inventory; a failed run leaves no success manifest."""
    plan, root = load_plan(plan_path)
    checksum(checkpoint_sha256, "checkpoint_sha256")
    checkpoint = Path(checkpoint).resolve()
    if not checkpoint.is_file() or digest(checkpoint) != checkpoint_sha256:
        raise ValueError("Model checkpoint SHA256 mismatch")
    if model_config not in CONFIGS or device not in ("cuda", "cpu"):
        raise ValueError("Choose a supported SAM2.1 config and cuda or cpu")
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise FileExistsError("Use a new mask output directory; existing masks are never replaced")
    backend = backend or sam2_masks
    objects = {obj["id"]: obj for obj in plan["objects"]}
    inventory, seen = [], set()
    output.mkdir(parents=True, exist_ok=False)
    try:
        with tempfile.TemporaryDirectory(prefix=".sam2-input-", dir=output) as temporary:
            frames_dir = Path(temporary)
            for index, frame in enumerate(plan["frames"]):
                with Image.open(root / frame["file"]) as image:
                    image.convert("RGB").save(frames_dir / f"{index:05d}.jpg", quality=100, subsampling=0)
            for index, ident, mask in backend(plan, frames_dir, checkpoint, model_config, device):
                integer(index, "backend frame", 0, len(plan["frames"]) - 1)
                if ident not in objects or (index, ident) in seen:
                    raise ValueError("Backend returned an unknown object or duplicate mask")
                mask = np.asarray(mask)
                if mask.shape != (plan["height"], plan["width"]) or mask.dtype != np.uint8 or not np.isin(mask, [0, 255]).all():
                    raise ValueError("Backend must return a source-size binary uint8 mask")
                seen.add((index, ident))
                active = any(a <= index < b for a, b in objects[ident]["intervals"])
                if not active:
                    mask = np.zeros_like(mask)
                target = output / f"object-{ident}" / f"{index:05d}.png"
                target.parent.mkdir(exist_ok=True)
                Image.fromarray(mask).save(target)
                source = plan["frames"][index]
                inventory.append({"file": str(target.relative_to(output)), "sha256": digest(target),
                                  "frame": index, "object_id": ident, "source_frame": source["source_frame"],
                                  "source_time": source["source_time"], "source_frame_sha256": source["sha256"],
                                  "active": active, "nonzero_pixels": int(np.count_nonzero(mask))})
        if len(seen) != len(objects) * len(plan["frames"]):
            raise ValueError("Backend did not return every selected object/frame pair")
        # Recheck input identity after inference, before publishing success.
        current, _ = load_plan(plan_path)
        if current != plan or digest(checkpoint) != checkpoint_sha256:
            raise ValueError("Inputs changed during segmentation")
        versions = {}
        for package in ("SAM-2", "torch", "numpy", "Pillow"):
            try:
                versions[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                versions[package] = None
        implementation = {}
        if backend is sam2_masks:
            package = importlib.util.find_spec("sam2")
            if package and package.submodule_search_locations:
                package_root = Path(next(iter(package.submodule_search_locations)))
                for extension in ("*.py", "*.yaml"):
                    for file in sorted(package_root.rglob(extension)):
                        implementation[str(file.relative_to(package_root))] = digest(file)
        report = {"schema": "video-use.subject-matte.v1", "complete": True,
                  "source_sha256": plan["source_sha256"], "plan": plan,
                  "width": plan["width"], "height": plan["height"],
                  "backend": {"name": f"{backend.__module__}.{backend.__qualname__}",
                              "sam2": backend is sam2_masks, "implementation_files": implementation},
                  "model": {"checkpoint_sha256": checkpoint_sha256, "config": model_config,
                            "device": device, "versions": versions,
                            "postprocessing": False if backend is sam2_masks else None,
                            "builder_options": {"apply_postprocessing": False,
                                                "hydra_overrides_extra": list(SAM2_HYDRA_OVERRIDES)} if backend is sam2_masks else None,
                            "mask_logit_threshold": MASK_LOGIT_THRESHOLD if backend is sam2_masks else None,
                            "input_conversion": "RGB JPEG quality100 4:4:4; original frame hashes retained"},
                  "masks": sorted(inventory, key=lambda x: (x["frame"], x["object_id"])),
                  "limits": "Binary segmentation seeds, not soft alpha or proof of a correct track. Inspect edges, occlusions and disappearance; save accepted corrections. Hidden backgrounds need separate authored plates. GPU results can vary across environments."}
        (output / "manifest.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
        return report
    except BaseException as error:
        (output / "incomplete.json").write_text(json.dumps({"complete": False, "error_type": type(error).__name__, "mask_count": len(inventory)}) + "\n")
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("plan", type=Path)
    parser.add_argument("--check", action="store_true", help="Validate all declared frame bytes and geometry without importing SAM2")
    parser.add_argument("-o", "--output", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--checkpoint-sha256")
    parser.add_argument("--model-config", choices=CONFIGS, default=CONFIGS[-1])
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    args = parser.parse_args(argv)
    try:
        if args.check:
            plan, _ = load_plan(args.plan)
            print(json.dumps({"valid": True, "frames": len(plan["frames"]), "objects": len(plan["objects"])}))
        else:
            if args.output is None or args.checkpoint is None or args.checkpoint_sha256 is None:
                parser.error("Rendering requires --output, --checkpoint and --checkpoint-sha256")
            report = run(args.plan, args.output, checkpoint=args.checkpoint, checkpoint_sha256=args.checkpoint_sha256,
                         model_config=args.model_config, device=args.device)
            print(json.dumps({"complete": report["complete"], "masks": len(report["masks"]), "manifest": str(args.output / "manifest.json")}))
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        parser.error(str(error))


if __name__ == "__main__":
    main()
