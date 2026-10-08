# Optional subject mask authoring

Use `helpers/subject_matte.py` to turn explicitly selected frames and point/box
prompts into source-bound binary masks with Meta SAM2.1. This is an optional
authoring stage. Ordinary replay should use inspected, saved masks without a
model download. It does not produce finished hair/motion-blur alpha, invent a
background, identify a person, or establish that a track is correct.

The validation path needs Python3.10+, NumPy and Pillow only. Model execution
also needs a separately installed official SAM2 environment, Torch, and a
checkpoint whose SHA256 you supply. No helper path downloads or installs them.

## Author one shot

Decode one continuous shot into original-size PNG/JPEG frames. Preserve actual
display-order presentation timestamps and source indices; do not infer them
from average frame rate. Normalize orientation before authoring coordinates.
Save a JSON plan beside its frame directory:

```json
{
  "version": 1,
  "source_sha256": "REPLACE_WITH_ORIGINAL_VIDEO_SHA256",
  "width": 1920,
  "height": 1080,
  "frames": [
    {"file": "frames/000.png", "sha256": "REPLACE_WITH_FRAME_SHA256",
     "source_frame": 180, "source_time": 6.006}
  ],
  "objects": [{"id": 1, "intervals": [[0, 1]]}],
  "prompts": [{"frame": 0, "object_id": 1,
               "points": [[940, 470], [400, 470]], "labels": [1, 0]}]
}
```

The example coordinates are illustrative; inspect the selected image and author
your own. All SHA256 values must be64lowercase hexadecimal characters. The
original video hash is a provenance claim: the helper opens and verifies the
frame files, not that original video or the decoding process. Retain the decoder
command, source hash and measured timestamp table with the project.

`frame` and active `intervals` index the selected frame list, starting at zero;
they are not original-video indices. Intervals are half-open `[first, stop)`.
SAM2 treats the selected JPEGs as consecutive model steps; it does not consume
the recorded source times. Sparse selections can lose temporal continuity, so
choose a suitable cadence and inspect the resulting track.
Each object needs a positive point or box on selected frame0. Split shots when
a new subject arrives, rather than labeling an absent object. Later prompts
are independent conditioning seeds submitted before forward propagation. Every
nonempty seed needs a positive point or box; optional negative points exclude
nearby pixels. They are not iterative corrections to an already tracked mask.
For an absent object use
`{"frame": 12, "object_id": 1, "empty": true}`. Combine one object's points
and box into one prompt per frame. Coordinates are original-image pixel centers.
A box is `[left, top, right, bottom]`, wholly inside the stored image.

Maximum selection:600frames,8objects,256prompts,120seconds,4096pixels per side,
8,388,608pixels per frame and1.2billion selected pixels. These are resource
bounds, not recommended edit lengths. Inactive intervals force empty output but
do not themselves reset or correct the predictor. Scene cuts require a new plan.

```sh
python -m helpers.subject_matte edit/mattes/plan.json --check
python -m helpers.subject_matte edit/mattes/plan.json \
  -o edit/mattes/seeds-v1 --checkpoint /absolute/path/sam2.1_hiera_large.pt \
  --checkpoint-sha256 2647878d5dfa5098f2f8649825738a9345572bae2d4350a2468587ece47dd318 \
  --model-config configs/sam2.1/sam2.1_hiera_l.yaml --device cuda
```

Supported official SAM2.1 configs are Hiera `t`, `s`, `b+`, and `l`; select a
matching checkpoint. The command requires a new output directory. Failed runs
leave `incomplete.json` and partial diagnostic masks, never a success manifest.
A completed manifest records source clocks, original frame hashes, per-object
mask hashes, checkpoint hash, package versions and backend implementation hashes.
Its `model.builder_options` records the exact SAM2 builder settings and
`model.mask_logit_threshold` records the strict output threshold. These fields
are null for a custom injected backend, whose internal settings are unknown.
Custom injected backends are explicitly identified and are not labeled as SAM2.

## Optional environment and review

An example pinned official backend is
[Meta SAM2](https://github.com/facebookresearch/sam2) at commit
`2b90b9f5ceec907a1c18123530e92e794ad901a4`, with Python3.12,
Torch2.5.1+cu124, torchvision0.20.1+cu124, NumPy2.1.3, Pillow11.0.0,
hydra-core1.3.2 and iopath0.1.10. Install it following the official repository's
requirements for your platform, and pin the exact commit/dependencies in the
project. Setting `SAM2_BUILD_CUDA=0` avoids the optional compiled extension.
The official Hiera Large checkpoint is available from
[Meta's checkpoint host](https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_large.pt).
Keep model/code licensing and source-footage provenance separate.

The adapter calls the official builder with `apply_postprocessing=False` and
these explicit `hydra_overrides_extra` settings:

```text
++model.fill_hole_area=0
++model.sam_mask_decoder_extra_args.dynamic_multimask_via_stability=true
++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_delta=0.05
++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_thresh=0.98
++model.binarize_mask_from_pts_for_mem_enc=true
```

The [pinned official video builder](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/sam2/build_sam.py#L94-L133)
normally enables both decoder/memory safeguards and small-hole filling together.
The explicit options retain the safeguards while keeping hole filling disabled.
Here `model.postprocessing=False` describes the builder flag, not the absence of
the separately enabled stability fallback or memory binarization.

When the single-mask prediction is unstable, the
[official decoder](https://github.com/facebookresearch/sam2/blob/2b90b9f5ceec907a1c18123530e92e794ad901a4/sam2/modeling/sam/mask_decoder.py#L225-L269)
can select the alternate mask with the highest predicted IoU. Stability compares
areas above logit thresholds +0.05 and -0.05; a ratio below0.98 triggers the
fallback. This is not98% object confidence or a guarantee of the correct subject.
Clicked-frame mask binarization controls the mask stored for later memory
encoding; it does not by itself repair an incorrect first-frame prediction.
Review and correct poorly placed seeds or boxes even when fallback is enabled.

Output PNGs use the strict binary test `logit > 0.0`, recorded separately from
the stability settings. The adapter stages RGB JPEGs at quality100 with4:4:4
sampling for SAM2, while retaining the original decoded-frame hashes. CUDA uses
BF16 autocast; different GPU environments can change boundaries. Preserve
accepted mask bytes rather than promising bit-identical future inference.

Inspect actual mask edges over both light and dark backgrounds, gaps between
limbs, foreground occlusions, fast motion and entry/exit. Save deliberate
corrections and any feathering separately from raw binary seeds. Inspect the
encoded composite through the difficult motion; a correct file hash does not
establish a good cutout. When satisfied, use the grayscale mask with
[subject layers](subject-layers.md). Map masks through the same
[source clock and pose transform](edit-motion.md) as their matching picture.
Background parallax still needs an independently authored, inspected clean plate.
