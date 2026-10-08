"""Exact picture clocks and image-only registration; never creates video/audio."""
from copy import deepcopy
from fractions import Fraction
import json

import numpy as np
from PIL import Image, ImageDraw
import pytest

from helpers import edit_motion as motion


def segment(frames=4, start=0, v0=1, v1=1, **extra):
    return dict(frames=frames, source_start=start, speed_start=v0, speed_end=v1, **extra)


def clock(segments=None, **kwargs):
    options = dict(timestamps=[0, '.25', '.5', '.75', 1, '1.25', '1.5', '1.75', 2],
                   source_end='2.25', segments=segments or [segment()], fps=4, audio_policy='separate')
    options.update(kwargs)
    return motion.plan_clock(**options)


def test_nonuniform_pts_select_containing_interval_without_average_fps():
    result = clock([segment(3, 2)], timestamps=[2, '2.04', '2.12', '2.20'], source_end='2.30', fps=10)
    assert [r['source_frame'] for r in result['frames']] == [0, 1, 3]
    assert [r['source_pts'] for r in result['frames']] == ['2', '51/25', '11/5']
    assert result['frames'][-1]['source_frame_end'] == '23/10'
    assert result['audio_processed'] is False


def test_rate_ramp_integral_and_hold_have_exact_boundary_and_exclusive_samples():
    result = clock([segment(v0=1, v1=3), segment(3, 2, 0, 0)])
    assert [r['source_time'] for r in result['frames']] == ['0', '5/16', '3/4', '21/16', '2', '2', '2']
    assert result['segments'][0]['source_end'] == '2'
    assert result['segments'][1]['output_start_frame'] == 4
    assert [r['source_frame'] for r in result['frames'][-3:]] == [8, 8, 8]
    assert result['duration'] == '7/4'


def test_deceleration_starts_moving_and_finishes_at_authored_hold():
    result = clock([segment(v0=2, v1=0), segment(2, 1, 0, 0)])
    assert [r['source_time'] for r in result['frames']] == ['0', '7/16', '3/4', '15/16', '1', '1']


def test_last_explicit_end_is_valid_boundary_but_never_a_sample():
    result = clock([segment(9)])
    assert result['segments'][0]['source_end'] == '9/4'
    assert result['frames'][-1]['source_time'] == '2'
    with pytest.raises(ValueError, match='outside'):
        clock([segment(1, '2.25', 0, 0)])
    result = clock([segment(2, '2.249', 0, 0)])
    assert [r['source_frame'] for r in result['frames']] == [8, 8]


def test_explicit_cut_allows_reorder_but_unmarked_jump_fails():
    with pytest.raises(ValueError, match='cut_before'):
        clock([segment(), segment(2, '.5')])
    result = clock([segment(), segment(2, '.5', cut_before=True)])
    assert result['frames'][4]['source_time'] == '1/2'
    assert result['segments'][1]['cut_before'] is True


def test_ntsc_rational_clock_does_not_accumulate_float_drift():
    result = clock([segment(3001)], timestamps=[Fraction(i, 24) for i in range(2600)],
                   source_end=Fraction(2600, 24), fps='30000/1001')
    assert result['frames'][-1]['output_time'] == '1001/10'
    assert result['segments'][0]['source_end'] == '3004001/30000'
    assert result['duration'] == '3004001/30000'
    assert all(Fraction(r['source_pts']) <= Fraction(r['source_time']) < Fraction(r['source_frame_end'])
               for r in result['frames'])


def test_float_artifact_is_not_silently_rounded_at_a_continuous_join():
    with pytest.raises(ValueError, match='Discontinuous'):
        clock([segment(3), segment(1, .1 + .2)], fps=10)
    result = clock([segment(3), segment(1, '3/10')], fps=10)
    assert result['frames'][3]['source_time'] == '3/10'


def test_negative_or_nonzero_origin_is_preserved_on_one_explicit_clock():
    result = clock([segment(2, '-1/10', 0, 0)], timestamps=['-.2', '-.1', '.1'], source_end='.2')
    assert result['frames'][0]['source_frame'] == 1
    assert result['frames'][0]['source_pts'] == '-1/10'


@pytest.mark.parametrize('changes', [
    {'timestamps': [0, 0, 1]}, {'timestamps': [0, 1, '.5']}, {'timestamps': []},
    {'timestamps': [0, float('nan')]}, {'source_end': 2}, {'fps': True}, {'fps': 0},
    {'audio_policy': 'copy'}, {'fps': '1e99999'},
    {'segments': [segment(frames=True)]}, {'segments': [segment(frames=0)]},
    {'segments': [segment(v0=-1)]}, {'segments': [segment(v1=17)]},
    {'segments': [segment(v1=float('inf'))]}, {'segments': [segment(start=-1)]},
    {'segments': [segment(frames=5, v0=3, v1=3)]},
    {'segments': [segment(cut_before=True)]}, {'segments': [segment(typo=1)]},
    {'segments': [segment(frames=14400, v0=0, v1=0)]},
])
def test_bad_clock_contracts_fail(changes):
    with pytest.raises(ValueError):
        clock(**changes)


def pose(source=None, target=None, **kwargs):
    source = [[10, 10], [30, 10], [10, 30]] if source is None else source
    target = source if target is None else target
    options = dict(source_landmarks=source, target_landmarks=target, source_size=[64, 64],
                   target_size=[128, 128], source_anchor={'frame': 17, 'timestamp': '17/24'})
    options.update(kwargs)
    return motion.fit_pose(**options)


def mapped(points, matrix):
    matrix = np.array(matrix)
    return np.array(points) @ matrix[:2, :2].T + matrix[:2, 2]


def test_two_landmarks_recover_scale_rotation_translation_and_anchor():
    source = [[10, 10], [30, 10]]
    target = [[30, 25], [30, 65]]
    result = pose(source, target, max_error=0)
    expected = np.array([[0, -2, 50], [2, 0, 5], [0, 0, 1]])
    np.testing.assert_allclose(result['source_to_target'], expected, atol=1e-12)
    np.testing.assert_allclose(mapped(target, result['target_to_source']), source, atol=1e-12)
    assert result['source_anchor'] == {'frame': 17, 'timestamp': '17/24'}


def test_redundant_similarity_landmarks_report_actual_fit_residuals():
    source = np.array([[10, 10], [30, 10], [10, 30], [30, 30]])
    angle = np.deg2rad(30)
    linear = 1.5 * np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    target = source @ linear.T + [30, 10]
    target[3] += [.1, -.1]
    result = pose(source, target, max_error=.2)
    actual = np.linalg.norm(mapped(source, result['source_to_target']) - target, axis=1)
    np.testing.assert_allclose(result['residuals_px'], actual)
    assert 0 < result['max_error_px'] < .2
    with pytest.raises(ValueError, match='residual'):
        pose(source, target, max_error=.001)


def test_affine_fits_nonuniform_scale_and_shear_with_three_points():
    source = [[5, 5], [45, 5], [10, 45], [40, 40]]
    matrix = [[1.2, .25, 10], [.1, .9, 12], [0, 0, 1]]
    result = pose(source, mapped(source, matrix), model='affine', max_error=0)
    np.testing.assert_allclose(result['source_to_target'], matrix, atol=1e-12)


@pytest.mark.parametrize('source,target,kwargs', [
    ([[10, 10]], [[10, 10]], {}),
    ([[10, 10], [10, 10]], [[10, 10], [30, 10]], {}),
    ([[10, 10], [30, 10]], [[10, 10], [10, 10]], {}),
    ([[10, 10], [30, 10]], [[10, 10], [30, 10]], {'model': 'affine'}),
    ([[5, 5], [10, 10], [20, 20]], [[5, 5], [10, 10], [20, 20]], {'model': 'affine'}),
    ([[5, 5], [25, 5], [5, 25]], [[5, 5], [25, 5], [45, 5]], {'model': 'affine'}),
    ([[5, 5], [25, 5], [5, 25]], [[25, 5], [5, 5], [25, 25]], {'model': 'affine'}),
    ([[5, 5], [25, 5], [5, 25]], [[25, 5], [5, 5], [25, 25]], {'max_error': 0}),
    ([[5, 5], [6, 6]], [[5, 5], [120, 120]], {}),
    ([[5, 5], [5.001, 5.001]], [[5, 5], [6, 6]], {}),
    ([[-1, 5], [25, 5]], [[5, 5], [25, 5]], {}),
    ([[5, 5], [64, 5]], [[5, 5], [25, 5]], {}),
    ([[True, 5], [25, 5]], [[5, 5], [25, 5]], {}),
    ([[5, 5], [25, float('nan')]], [[5, 5], [25, 5]], {}),
])
def test_invalid_or_degenerate_landmarks_fail(source, target, kwargs):
    with pytest.raises(ValueError):
        pose(source, target, **kwargs)


@pytest.mark.parametrize('changes', [
    {'source_anchor': {'frame': True, 'timestamp': 0}},
    {'source_anchor': {'frame': 0, 'timestamp': float('nan')}},
    {'source_anchor': {'frame': 0, 'timestamp': 0, 'sha256': 'invalid'}},
    {'source_size': [64.0, 64]}, {'target_size': [20000, 20000]},
    {'max_error': -1}, {'max_error': float('nan')}, {'model': 'homography'},
])
def test_pose_metadata_is_bounded(changes):
    with pytest.raises(ValueError):
        pose(**changes)


def test_image_warp_aligns_real_pixels_at_rotation_endpoints_without_half_pixel_shift():
    image = Image.new('L', (64, 64))
    image.putpixel((10, 10), 200)
    image.putpixel((30, 10), 100)
    original = image.tobytes()
    result = pose([[10, 10], [30, 10]], [[30, 25], [30, 65]])
    warped = motion.warp_pose(image, result)
    assert warped.getpixel((30, 25)) == 200
    assert warped.getpixel((30, 65)) == 100
    assert image.tobytes() == original


@pytest.mark.parametrize('resample', ['nearest', 'bilinear'])
def test_rgba_alpha_and_separate_matte_share_identical_geometry(resample):
    matte = Image.new('L', (64, 64))
    ImageDraw.Draw(matte).polygon([(8, 7), (39, 13), (24, 47)], fill=255)
    layer = Image.new('RGBA', matte.size, (200, 40, 20, 0))
    layer.putalpha(matte)
    source = [[10, 10], [30, 10], [10, 30]]
    transform = [[1.2, -.4, 23.25], [.4, 1.2, 6.5], [0, 0, 1]]
    result = pose(source, mapped(source, transform))
    separate = motion.warp_pose(matte, result, resample=resample)
    together = motion.warp_pose(layer, result, resample=resample)
    assert separate.tobytes() == together.getchannel('A').tobytes()
    rgba = np.asarray(together)
    visible = rgba[:, :, 3] > 64
    np.testing.assert_allclose(rgba[visible, :3], np.broadcast_to([200, 40, 20], (visible.sum(), 3)), atol=3)
    assert together.getpixel((127, 127)) == (0, 0, 0, 0)


def test_identity_image_warp_is_exact_for_all_supported_modes():
    random = np.random.default_rng(4)
    result = pose(target_size=[64, 64])
    for mode, shape in [('L', (64, 64)), ('RGB', (64, 64, 3)), ('RGBA', (64, 64, 4))]:
        pixels = random.integers(0, 256, shape, dtype=np.uint8)
        image = Image.fromarray(pixels, mode)
        assert motion.warp_pose(image, result, resample='nearest').tobytes() == image.tobytes()


def test_warp_rejects_mismatched_geometry_and_tampered_transform():
    image = Image.new('L', (64, 64))
    with pytest.raises(ValueError, match='source-sized'):
        motion.warp_pose(Image.new('L', (63, 64)), pose())
    wrong = deepcopy(pose())
    wrong['source_to_target'][0][2] += 2
    with pytest.raises(ValueError, match='inverse'):
        motion.warp_pose(image, wrong)
    wrong['source_to_target'][0][0] = 0
    with pytest.raises(ValueError, match='degenerate'):
        motion.warp_pose(image, wrong)


def test_json_cli_is_read_only_and_outputs_consumable_plan(tmp_path, capsys):
    spec = dict(timestamps=[0, '.1', '.25'], source_end='.4',
                segments=[segment(3, 0, 1, 1)], fps=10, audio_policy='silent')
    path = tmp_path / 'clock.json'
    path.write_text(json.dumps(spec))
    before = path.read_bytes()
    motion.main(['clock', str(path)])
    result = json.loads(capsys.readouterr().out)
    assert [r['source_frame'] for r in result['frames']] == [0, 1, 1]
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]
