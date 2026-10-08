"""Contract tests use synthetic pixels; they do not establish SAM2 mask quality."""
import copy
from contextlib import nullcontext
from importlib.machinery import ModuleSpec
import json
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from helpers import subject_matte as matte


@pytest.fixture
def shot(tmp_path):
    (tmp_path / "frames").mkdir()
    frames = []
    for n, t in enumerate((0., .04, .09)):
        p = tmp_path / "frames" / f"{n}.png"
        image = np.zeros((24, 32, 3), dtype=np.uint8)
        image[6:18, 8+n:16+n] = [255, 80, 20]
        Image.fromarray(image).save(p)
        frames.append({"file": f"frames/{n}.png", "sha256": matte.digest(p), "source_frame": 10+n, "source_time": t})
    plan = {"version": 1, "source_sha256": "a"*64, "width": 32, "height": 24,
            "frames": frames, "objects": [{"id": 1, "intervals": [[0, 2]]}],
            "prompts": [{"frame": 0, "object_id": 1, "points": [[11, 12], [1, 1]], "labels": [1, 0]}]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))
    checkpoint = tmp_path / "test-checkpoint.bin"
    checkpoint.write_bytes(b"not a model; injected contract backend only")
    return plan, path, checkpoint


def fake_masks(plan, directory, checkpoint, config, device):
    assert sorted(p.name for p in directory.iterdir()) == ["00000.jpg", "00001.jpg", "00002.jpg"]
    for n in range(3):
        with Image.open(directory / f"{n:05d}.jpg") as image:
            assert image.size == (32, 24)
        mask = np.zeros((24, 32), dtype=np.uint8)
        mask[6:18, 8+n:16+n] = 255
        yield n, 1, mask


def test_masks_bind_to_each_actual_frame_and_half_open_activity(shot, tmp_path):
    plan, path, checkpoint = shot
    report = matte.run(path, tmp_path / "masks", checkpoint=checkpoint,
                       checkpoint_sha256=matte.digest(checkpoint), backend=fake_masks)
    assert report["complete"]
    assert report["backend"]["sam2"] is False
    assert report["model"]["builder_options"] is None
    assert report["model"]["mask_logit_threshold"] is None
    assert [m["source_time"] for m in report["masks"]] == [0., .04, .09]
    assert [m["source_frame_sha256"] for m in report["masks"]] == [f["sha256"] for f in plan["frames"]]
    assert [m["nonzero_pixels"] for m in report["masks"]] == [96, 96, 0]
    for entry in report["masks"]:
        target = tmp_path / "masks" / entry["file"]
        assert matte.digest(target) == entry["sha256"]
        assert Image.open(target).mode == "L"
    assert not list((tmp_path / "masks").glob(".sam2-input-*"))
    assert json.loads((tmp_path / "masks/manifest.json").read_text())["complete"]


def test_sam2_builder_receives_explicit_fallback_without_hole_fill_and_receipt_matches(shot, tmp_path, monkeypatch):
    """Exercise the real adapter/run path, spying only on the optional model API."""
    _, path, checkpoint = shot
    calls = {}

    class Tensor:
        def __init__(self, values):
            self.values = np.asarray(values)

        def __getitem__(self, key):
            return Tensor(self.values[key])

        def __gt__(self, threshold):
            return Tensor(self.values > threshold)

        def cpu(self):
            return self

        def numpy(self):
            return self.values

    def init_state(**options):
        calls["state"] = options
        assert sorted(p.name for p in Path(options["video_path"]).iterdir()) == ["00000.jpg", "00001.jpg", "00002.jpg"]
        return {"ready": True}

    def propagate(state, **options):
        assert state == {"ready": True}
        assert options == {"start_frame_idx": 0}
        for n in range(3):
            logits = np.full((1, 1, 24, 32), -1., dtype=np.float32)
            logits[0, 0, 0, :4] = [-1., 0., np.nextafter(np.float32(0), np.float32(1)), 1.]
            yield n, [1], Tensor(logits)

    def builder(config, checkpoint_path, **options):
        calls["builder"] = {"config": config, "checkpoint": checkpoint_path, **options}
        return SimpleNamespace(init_state=init_state,
                               add_new_points_or_box=lambda **kwargs: calls.setdefault("prompt", kwargs),
                               propagate_in_video=propagate)

    package = ModuleType("sam2")
    package.__spec__ = ModuleSpec("sam2", loader=None, is_package=True)
    module = ModuleType("sam2.build_sam")
    module.build_sam2_video_predictor = builder
    monkeypatch.setitem(sys.modules, "sam2", package)
    monkeypatch.setitem(sys.modules, "sam2.build_sam", module)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        inference_mode=nullcontext, isfinite=lambda value: np.isfinite(value.values)))
    output = tmp_path / "actual-adapter"
    report = matte.run(path, output, checkpoint=checkpoint,
                       checkpoint_sha256=matte.digest(checkpoint), device="cpu")

    expected_overrides = [
        "++model.fill_hole_area=0",
        "++model.sam_mask_decoder_extra_args.dynamic_multimask_via_stability=true",
        "++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_delta=0.05",
        "++model.sam_mask_decoder_extra_args.dynamic_multimask_stability_thresh=0.98",
        "++model.binarize_mask_from_pts_for_mem_enc=true",
    ]
    assert calls["builder"] == {"config": "configs/sam2.1/sam2.1_hiera_l.yaml",
                                "checkpoint": str(checkpoint.resolve()), "device": "cpu",
                                "apply_postprocessing": False, "hydra_overrides_extra": expected_overrides}
    assert calls["state"]["offload_video_to_cpu"] is True
    assert calls["state"]["offload_state_to_cpu"] is True
    assert calls["prompt"]["points"].dtype == np.float32
    assert calls["prompt"]["labels"].dtype == np.int32
    assert report["backend"]["sam2"] is True
    assert report["model"]["builder_options"] == {
        "apply_postprocessing": calls["builder"]["apply_postprocessing"],
        "hydra_overrides_extra": calls["builder"]["hydra_overrides_extra"],
    }
    assert report["model"]["postprocessing"] is False
    assert report["model"]["mask_logit_threshold"] == 0.0
    assert json.loads((output / "manifest.json").read_text())["model"] == report["model"]
    assert np.asarray(Image.open(output / "object-1/00000.png"))[0, :4].tolist() == [0, 0, 255, 255]
    assert [m["nonzero_pixels"] for m in report["masks"]] == [2, 2, 0]


@pytest.mark.parametrize("change", [
    lambda p: p.update(version=True),
    lambda p: p.update(unrecognized=True),
    lambda p: p["frames"][1].update(source_time=0),
    lambda p: p["frames"][1].update(source_frame=10),
    lambda p: p["frames"][0].update(file="../escape.png"),
    lambda p: p["frames"][0].update(sha256="z"*64),
    lambda p: p["objects"][0].update(intervals=[[0, 2], [1, 3]]),
    lambda p: p["prompts"][0].update(labels=[0, 0]),
    lambda p: p["prompts"].append({"frame": 1, "object_id": 1, "points": [[1, 1]], "labels": [0]}),
    lambda p: p["prompts"][0].update(points=[[32, 12], [1, 1]]),
    lambda p: p["prompts"][0].update(labels=[True, 0]),
    lambda p: p["prompts"][0].update(frame=1),
    lambda p: p["prompts"].append(copy.deepcopy(p["prompts"][0])),
])
def test_reject_ambiguous_clocks_and_invalid_prompts(shot, change):
    plan, _, _ = shot
    change(plan)
    with pytest.raises(ValueError):
        matte.validate_plan(plan)


def test_valid_box_and_later_explicit_absence(shot):
    plan, _, _ = shot
    plan["prompts"] = [{"frame": 0, "object_id": 1, "box": [8, 6, 16, 18]},
                       {"frame": 2, "object_id": 1, "empty": True}]
    assert matte.validate_plan(plan) == plan


@pytest.mark.parametrize("bad_logit", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_model_output_is_not_published_as_a_mask(shot, tmp_path, monkeypatch, bad_logit):
    _, path, checkpoint = shot
    logits = np.zeros((1, 1, 24, 32), dtype=np.float32)
    logits[0, 0, 8, 12] = bad_logit
    predictor = SimpleNamespace(
        init_state=lambda **kwargs: {},
        add_new_points_or_box=lambda **kwargs: None,
        propagate_in_video=lambda *args, **kwargs: iter([(0, [1], logits)]),
    )
    torch = SimpleNamespace(inference_mode=nullcontext, isfinite=np.isfinite,
                            float32=np.float32, int32=np.int32)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "sam2.build_sam", SimpleNamespace(
        build_sam2_video_predictor=lambda *args, **kwargs: predictor))
    output = tmp_path / "failed-model"
    with pytest.raises(RuntimeError, match="nonfinite"):
        matte.run(path, output, checkpoint=checkpoint,
                  checkpoint_sha256=matte.digest(checkpoint), device="cpu")
    assert not (output / "manifest.json").exists()
    assert json.loads((output / "incomplete.json").read_text())["mask_count"] == 0


def test_tampered_source_fails_before_output_creation(shot, tmp_path):
    _, path, checkpoint = shot
    Image.new("RGB", (32, 24), "blue").save(tmp_path / "frames/0.png")
    with pytest.raises(ValueError, match="identity"):
        matte.run(path, tmp_path / "out", checkpoint=checkpoint,
                  checkpoint_sha256=matte.digest(checkpoint), backend=fake_masks)
    assert not (tmp_path / "out").exists()


def test_geometry_and_exif_must_match_stored_coordinate_system(shot, tmp_path):
    plan, path, _ = shot
    p = tmp_path / "frames/0.png"
    Image.new("RGB", (31, 24)).save(p)
    plan["frames"][0]["sha256"] = matte.digest(p)
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match="geometry"):
        matte.load_plan(path)


def test_incomplete_inference_never_has_success_manifest(shot, tmp_path):
    _, path, checkpoint = shot
    def interrupted(*args):
        yield 0, 1, np.zeros((24, 32), dtype=np.uint8)
    out = tmp_path / "incomplete"
    with pytest.raises(ValueError, match="every selected"):
        matte.run(path, out, checkpoint=checkpoint, checkpoint_sha256=matte.digest(checkpoint), backend=interrupted)
    assert not (out / "manifest.json").exists()
    assert json.loads((out / "incomplete.json").read_text())["complete"] is False


def test_reject_soft_or_wrong_sized_backend_masks(shot, tmp_path):
    _, path, checkpoint = shot
    def soft(*args):
        yield 0, 1, np.full((24, 32), 127, dtype=np.uint8)
    with pytest.raises(ValueError, match="binary uint8"):
        matte.run(path, tmp_path / "soft", checkpoint=checkpoint,
                  checkpoint_sha256=matte.digest(checkpoint), backend=soft)


def test_existing_output_preserved_and_checkpoint_verified(shot, tmp_path):
    _, path, checkpoint = shot
    out = tmp_path / "kept"
    out.mkdir()
    (out / "user.txt").write_text("keep")
    with pytest.raises(FileExistsError):
        matte.run(path, out, checkpoint=checkpoint, checkpoint_sha256=matte.digest(checkpoint), backend=fake_masks)
    assert (out / "user.txt").read_text() == "keep"
    with pytest.raises(ValueError, match="checkpoint"):
        matte.run(path, tmp_path / "bad", checkpoint=checkpoint, checkpoint_sha256="0"*64, backend=fake_masks)
    assert not (tmp_path / "bad").exists()


def test_offline_check_cli_needs_no_model(shot):
    _, path, _ = shot
    result = subprocess.run([sys.executable, "-m", "helpers.subject_matte", str(path), "--check"],
                            text=True, capture_output=True, check=True)
    assert json.loads(result.stdout) == {"valid": True, "frames": 3, "objects": 1}
