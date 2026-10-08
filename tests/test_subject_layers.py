"""Synthetic matte registration, occlusion, premultiplied edges and replay IO."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw
import pytest

from helpers import subject_layers as layers


def asset(path, size=(32, 24), kind="image", digest="a" * 64, **extra):
    return {"path": path, "size": list(size), "kind": kind, "sha256": digest, **extra}


def config():
    return {"version": 1, "canvas": [32, 24], "fps": 30, "frame_count": 30,
            "assets": {"source": asset("source.png")},
            "layers": [{"id": "base", "image": "source", "frames": [0, 30]}]}


def premult(color, size=(32, 24)):
    return layers.premultiply(Image.new("RGBA", size, color))


def test_porter_duff_with_translucent_destination_and_input_preservation():
    bg = np.array([[[0., 0., .5, .5]]], np.float32)
    fg = np.array([[[.5, 0., 0., .5]]], np.float32)
    before = bg.copy(), fg.copy()
    output = layers.over(bg, fg)
    np.testing.assert_allclose(output, [[[.5, 0, .25, .75]]])
    np.testing.assert_array_equal(bg, before[0])
    np.testing.assert_array_equal(fg, before[1])
    assert tuple(np.asarray(layers.straight_image(output))[0, 0]) == (170, 0, 85, 191)


def test_subject_restores_over_artwork_but_dialogue_stays_last():
    c = config()
    c["assets"].update({"alpha": asset("mask.png", kind="mask", digest="b" * 64,
                                       source_sha256="a" * 64),
                        "type": asset("type.png", digest="c" * 64),
                        "caption": asset("caption.png", digest="d" * 64)})
    c["layers"] += [{"id": "artwork", "image": "type", "frames": [0, 30]},
                    {"id": "subject", "image": "source", "mask": "alpha", "frames": [0, 30]},
                    {"id": "dialogue", "image": "caption", "frames": [0, 30], "role": "caption"}]
    source = np.zeros((24, 32, 3), np.uint8)
    source[:] = (20, 30, 90)
    source[4:20, 12:22] = (220, 60, 30)
    alpha = np.zeros((24, 32), np.uint8)
    alpha[4:20, 12:22] = 255
    alpha[10:13, 16:18] = 0  # true negative space must reveal artwork
    type_image = np.zeros((24, 32, 4), np.uint8)
    type_image[8:16] = (40, 230, 60, 255)
    caption = np.zeros_like(type_image)
    caption[18:21, 8:27] = (255, 255, 255, 255)
    buffers = {"source": layers.premultiply(source), "alpha": layers.mask_alpha(alpha),
               "type": layers.premultiply(type_image), "caption": layers.premultiply(caption)}
    originals = {k: v.copy() for k, v in buffers.items()}
    out = np.asarray(layers.render_frame(layers.validate_config(c), buffers, 10))
    assert tuple(out[9, 14]) == (220, 60, 30, 255)
    assert tuple(out[11, 16]) == (40, 230, 60, 255)
    assert tuple(out[9, 5]) == (40, 230, 60, 255)
    assert tuple(out[19, 14]) == (255, 255, 255, 255)
    for k in buffers:
        np.testing.assert_array_equal(buffers[k], originals[k])


def test_premultiplied_affine_has_no_hidden_rgb_fringe():
    rgba = np.zeros((20, 20, 4), np.uint8)
    rgba[..., :3] = (0, 0, 255)  # deliberately toxic blue RGB at zero alpha
    rgba[5:15, 7:13] = (255, 0, 0, 255)
    source = layers.premultiply(rgba)
    out = layers.transform_layer(source, (40, 40),
                                 {"position": [19.3, 20.4], "scale": [1.3, 1.3], "rotation": 17},
                                 anchor=(10, 10))
    assert np.any((out[..., 3] > .05) & (out[..., 3] < .95))
    assert np.max(np.abs(out[..., 2])) == 0
    np.testing.assert_allclose(out[..., 0], out[..., 3], atol=1e-6)
    np.testing.assert_array_equal(layers.transform_layer(source, (20, 20), {}), source)


def test_existing_image_alpha_is_multiplied_not_replaced_by_matte():
    source = premult((200, 100, 50, 128), (2, 2))
    out = layers.masked_layer(source, np.full((2, 2), .5, np.float32))
    np.testing.assert_allclose(out, source * .5)
    assert tuple(np.asarray(layers.straight_image(out))[0, 0]) == (200, 100, 50, 64)


@pytest.mark.parametrize("sampling", ["bilinear", "bicubic"])
def test_transformed_nonsaturated_color_does_not_brighten_at_alpha_overshoot(sampling):
    matte = Image.new("L", (80, 80))
    ImageDraw.Draw(matte).polygon([(7, 65), (41, 9), (72, 61)], fill=255)
    color = (200, 40, 20)
    source = layers.masked_layer(premult((*color, 255), (80, 80)), matte)
    output = layers.transform_layer(source, (140, 140),
                                    {"position": [70.3, 65.7], "scale": [1.37, 1.37], "rotation": 23},
                                    (40, 40), sampling)
    rgba = np.asarray(layers.straight_image(output))
    visible = rgba[..., 3] > 0
    assert np.count_nonzero((rgba[..., 3] > 0) & (rgba[..., 3] < 255)) > 100
    error = np.abs(rgba[visible, :3].astype(int) - np.array(color))
    assert error.max() <= 1


def test_mask_and_rgb_transform_match_at_different_canvas_sizes():
    mask = np.zeros((8, 10), np.uint8)
    mask[2:6, 3:7] = 255
    source = layers.masked_layer(premult((240, 120, 40, 255), (10, 8)), mask)
    pose = {"position": [20, 16], "scale": [2, 2], "rotation": 90}
    alpha = layers.transform_mask(mask, (40, 32), pose, (5, 4), "nearest")
    out = layers.transform_layer(source, (40, 32), pose, (5, 4), "nearest")
    np.testing.assert_array_equal(out[..., 3], alpha)
    assert np.sum(alpha) == 64
    # Clockwise90° moves a source-right marker down from the pivot.
    marker = np.zeros((8, 10), np.uint8)
    marker[3:5, 7:9] = 255
    rotated = layers.transform_mask(marker, (40, 32), pose, (5, 4), "nearest")
    ys, xs = np.where(rotated > .5)
    assert ys.mean() > 16 and abs(xs.mean() - 19.5) < 1


def test_transparent_canvas_edges_do_not_reflect_or_stretch_foreground():
    source = premult((255, 100, 40, 255), (4, 4))
    out = layers.transform_layer(source, (8, 8), {"position": [-2, 2]}, resample="nearest")
    assert np.count_nonzero(out[..., 3]) == 8
    assert np.all(out[:, 2:, :] == 0)


def test_center_based_fit_converts_to_edge_based_pose_without_half_pixel_drift():
    # A center-based 2x fit sends source pixel center(2,3) to target(7,10).
    center_matrix = np.array([[2., 0, 3], [0, 2., 4], [0, 0, 1]])
    shift = np.array([[1., 0, .5], [0, 1., .5], [0, 0, 1]])
    edge_matrix = shift @ center_matrix @ np.linalg.inv(shift)
    mask = np.zeros((10, 10), np.float32)
    mask[3, 2] = 1
    warped = layers.transform_mask(mask, (30, 30),
                                   {"position": edge_matrix[:2, 2].tolist(), "scale": [2, 2]})
    yy, xx = np.indices(warped.shape)
    assert np.sum(xx * warped) / warped.sum() == pytest.approx(7)
    assert np.sum(yy * warped) / warped.sum() == pytest.approx(10)


def test_subject_shaped_wipe_and_inverted_reveal():
    c = config()
    c["resample"] = "nearest"
    c["assets"].update({"mask": asset("mask.png", kind="mask", digest="b" * 64, source_sha256="a" * 64),
                        "incoming": asset("incoming.png", digest="c" * 64)})
    c["layers"].append({"id": "incoming", "image": "incoming", "frames": [0, 30],
                         "reveal": {"mask": "mask", "pose": {"position": [5, 0]}}})
    alpha = np.zeros((24, 32), np.float32)
    alpha[4:20, 3:11] = 1
    alpha[10:14, 6:8] = 0
    buffers = {"source": premult((255, 0, 0, 255)), "incoming": premult((0, 0, 255, 255)), "mask": alpha}
    out = np.asarray(layers.render_frame(layers.validate_config(c), buffers, 0))
    assert tuple(out[7, 9]) == (0, 0, 255, 255)
    assert tuple(out[11, 11]) == (255, 0, 0, 255)
    c["layers"][1]["reveal"]["invert"] = True
    inv = np.asarray(layers.render_frame(layers.validate_config(c), buffers, 0))
    assert tuple(inv[7, 9]) == (255, 0, 0, 255)
    assert tuple(inv[11, 11]) == (0, 0, 255, 255)


def test_half_open_frames_and_exact_return_to_source_pose():
    c = config()
    c["layers"][0]["frames"] = [2, 28]
    c["layers"][0]["keyframes"] = [
        {"frame": 2}, {"frame": 14, "position": [4, 0]}, {"frame": 27}]
    spec = layers.validate_config(c)
    buffers = {"source": premult((170, 40, 90, 255))}
    first = layers.render_frame(spec, buffers, 2)
    last = layers.render_frame(spec, buffers, 27)
    assert first.tobytes() == last.tobytes()
    assert not layers.render_frame(spec, buffers, 1).getbbox()
    assert not layers.render_frame(spec, buffers, 28).getbbox()
    assert layers.render_frame(spec, buffers, 14).tobytes() != first.tobytes()
    with pytest.raises(ValueError):
        layers.render_frame(spec, buffers, 30)


def test_keyframe_hold_releases_exactly_and_smoothstep_is_seek_independent():
    c = config()
    c["layers"][0]["keyframes"] = [
        {"frame": 0, "position": [1, 0], "ease": "hold"},
        {"frame": 10, "position": [11, 0], "ease": "smoothstep"},
        {"frame": 30, "position": [31, 0]}]
    motion = layers.validate_config(c)["layers"][0]
    assert layers.pose_at(motion, 9)["position"][0] == 1
    assert layers.pose_at(motion, 10)["position"][0] == 11
    assert layers.pose_at(motion, 15)["position"][0] == pytest.approx(14.125)
    assert layers.pose_at(motion, 20)["position"][0] == 21
    assert layers.pose_at(motion, 15)["position"][0] == pytest.approx(14.125)
    with pytest.raises(ValueError):
        layers.pose_at(motion, 31)


@pytest.mark.parametrize("change", [
    {"version": True}, {"canvas": [0, 24]}, {"canvas": [8192, 8192]},
    {"fps": float("nan")}, {"fps": True}, {"frame_count": 1.5},
    {"background": [0, 0, 0, 256]}, {"automatic_segmentation": True},
    {"resample": "nearestish"}, {"assets": {}}, {"layers": []},
    {"resample": []},
])
def test_invalid_scene_inputs_rejected_without_mutation(change):
    c = config()
    c.update(change)
    with pytest.raises(ValueError):
        layers.validate_config(c)


@pytest.mark.parametrize("change", [
    {"frames": [0, 31]}, {"frames": [3, 3]}, {"frames": [False, 30]},
    {"anchor": [33, 0]}, {"pose": {"scale": [0, 1]}},
    {"pose": {"position": [float("inf"), 0]}},
    {"keyframes": [{"frame": 1}, {"frame": 30}]},
    {"keyframes": [{"frame": 0}, {"frame": 28}]},
    {"keyframes": [{"frame": 0}, {"frame": 0}]},
    {"pose": {}, "keyframes": [{"frame": 0}, {"frame": 30}]},
    {"image": []}, {"mask": []}, {"role": []},
])
def test_invalid_layer_geometry_and_uncovered_clocks(change):
    c = config()
    c["layers"][0].update(change)
    with pytest.raises(ValueError):
        layers.validate_config(c)


def test_mask_identity_geometry_and_caption_order_fail_closed():
    c = config()
    c["assets"]["mask"] = asset("mask.png", kind="mask", source_sha256="b" * 64)
    with pytest.raises(ValueError, match="bind"):
        layers.validate_config(c)
    c["assets"]["mask"]["source_sha256"] = "a" * 64
    c["assets"]["mask"]["size"] = [31, 24]
    with pytest.raises(ValueError, match="dimensions"):
        layers.validate_config(c)
    c = config()
    c["layers"][0]["role"] = "caption"
    c["layers"].append({"id": "bad-after-dialogue", "image": "source", "frames": [0, 30]})
    with pytest.raises(ValueError, match="captions must be last"):
        layers.validate_config(c)


def test_mask_cannot_be_reused_on_different_source_or_source_frame():
    c = config()
    c["assets"].update({"other": asset("other.png", digest="c" * 64),
                        "mask": asset("mask.png", kind="mask", source_sha256="a" * 64)})
    c["layers"][0].update(image="other", mask="mask")
    with pytest.raises(ValueError, match="this layer"):
        layers.validate_config(c)
    c["layers"][0]["image"] = "source"
    c["assets"]["source"]["source_frame"] = 180
    c["assets"]["mask"]["source_frame"] = 181
    with pytest.raises(ValueError, match="source_frame"):
        layers.validate_config(c)


def test_color_mask_and_nonpremultiplied_integer_arrays_rejected():
    with pytest.raises(ValueError, match="grayscale"):
        layers.mask_alpha(Image.new("RGB", (2, 2), "white"))
    with pytest.raises(ValueError, match="premultiplied"):
        layers.transform_layer(np.ones((2, 2, 4), np.uint8), (2, 2), {})


@pytest.mark.parametrize("path", ["../outside.png", "/tmp/outside.png", "folder/../image.png", "./image.png"])
def test_asset_paths_cannot_escape_manifest(path):
    c = config()
    c["assets"]["source"]["path"] = path
    with pytest.raises(ValueError):
        layers.validate_config(c)


def write_scene(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (32, 24), (160, 80, 20, 128)).save(source)
    c = config()
    c["assets"]["source"]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    scene = tmp_path / "scene.json"
    scene.write_text(json.dumps(c))
    return scene, c


def test_file_hash_geometry_and_symlink_checks(tmp_path):
    scene, c = write_scene(tmp_path)
    original = copy.deepcopy(c)
    spec = layers.validate_config(c)
    assert c == original
    assert layers.load_assets(spec, scene.parent)["source"].shape == (24, 32, 4)
    spec["assets"]["source"]["size"] = [31, 24]
    with pytest.raises(ValueError, match="dimensions"):
        layers.load_assets(spec, scene.parent)
    spec = layers.validate_config(c)
    (tmp_path / "source.png").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA256"):
        layers.load_assets(spec, scene.parent)
    outside = tmp_path.parent / "outside-mask-proof.png"
    outside.write_bytes(b"outside")
    link = tmp_path / "escape.png"
    link.symlink_to(outside)
    spec["assets"]["source"]["path"] = "escape.png"
    with pytest.raises(ValueError, match="within"):
        layers.load_assets(spec, scene.parent)


def test_buffer_contract_rejects_invalid_alpha_or_mismatched_geometry():
    with pytest.raises(ValueError, match="premultiplied"):
        layers.over(np.ones((2, 2, 4), np.float32), np.full((2, 2, 4), np.nan, np.float32))
    rgba = np.ones((2, 2, 4), np.float32)
    rgba[..., 3] = .1
    with pytest.raises(ValueError, match="premultiplied"):
        layers.straight_image(rgba)
    with pytest.raises(ValueError, match="dimensions"):
        layers.masked_layer(premult((0, 0, 0, 255)), np.ones((20, 10), np.float32))
    with pytest.raises(ValueError, match="finite"):
        layers.mask_alpha(np.array([[float("inf")]], np.float32))
    with pytest.raises(ValueError, match="geometry"):
        layers.render_frame(layers.validate_config(config()), {"source": premult((0, 0, 0, 255), (31, 24))}, 0)


def test_cli_is_standalone_writes_exact_frames_receipt_and_never_overwrites(tmp_path):
    scene, _ = write_scene(tmp_path)
    helper = Path(layers.__file__).resolve()
    before = {p.name: p.read_bytes() for p in [scene, tmp_path / "source.png"]}
    command = [sys.executable, str(helper), str(scene)]
    checked = subprocess.run(command + ["--check"], cwd=tmp_path, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    assert set(p.name for p in tmp_path.iterdir()) == {"source.png", "scene.json"}
    output = tmp_path / "nested" / "proof"
    rendered = subprocess.run(command + ["--frames", "29,0,15", "--output-dir", str(output)],
                              cwd=tmp_path, capture_output=True, text=True)
    assert rendered.returncode == 0, rendered.stderr
    receipt = json.loads((output / "receipt.json").read_text())
    assert [item["frame"] for item in receipt["outputs"]] == [29, 0, 15]
    for item in receipt["outputs"]:
        path = output / item["file"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"]
        with Image.open(path) as image:
            assert image.mode == "RGBA" and image.size == (32, 24)
            assert image.getpixel((0, 0)) == (160, 80, 20, 128)
    again = subprocess.run(command + ["--output-dir", str(output)], cwd=tmp_path, capture_output=True)
    assert again.returncode == 2
    invalid = subprocess.run(command + ["--frames", "30", "--output-dir", str(tmp_path / "invalid")],
                             cwd=tmp_path, capture_output=True)
    assert invalid.returncode == 2 and not (tmp_path / "invalid").exists()
    assert all((tmp_path / name).read_bytes() == data for name, data in before.items())
