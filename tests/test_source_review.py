"""Regression cases from the source helper review."""
import json
import os
import subprocess
import sys
from pathlib import Path
import pytest
from edit_clock import allocate_frames, frame_to_sample, seconds_to_frame, seconds_to_sample
from edit_io import last_json, save_json, sha256
from project_state import record, view


# nested measurements retain the complete outer object
def test_nested_measurement():
    assert last_json('log {"old": 1} noise {"outer": {"value": 2}}') == {"outer": {"value": 2}}


# every conversion rejects clocks that cannot advance
@pytest.mark.parametrize('rate', [0, -1, float('nan'), float('inf'), True])
def test_invalid_rates(rate):
    for call in (lambda: frame_to_sample(1, fps=rate), lambda: seconds_to_frame(1, fps=rate), lambda: seconds_to_sample(1, rate=rate)):
        with pytest.raises(ValueError):
            call()


# short positive shots still receive a frame and decimal rates remain usable
def test_small_shot_budget():
    assert allocate_frames(3, [100, 1, 1]) == [1, 1, 1]
    assert frame_to_sample(1, rate=48000.0) == 1600
    with pytest.raises(ValueError):
        allocate_frames(3.0, [1, 1])


# existing aliases are rejected before either source command reads media
@pytest.mark.parametrize('helper', ['source_scan.py', 'find_shot.py'])
def test_hardlink_report_preserves_input(tmp_path, helper):
    pytest.importorskip('cv2')
    source = tmp_path / 'source'; source.write_bytes(b'original source')
    out = tmp_path / 'report'; os.link(source, out)
    script = Path(__file__).resolve().parents[1] / 'helpers' / helper
    args = [str(source)] * (2 if helper == 'find_shot.py' else 1)
    result = subprocess.run([sys.executable, str(script), *args, '--out', str(out)], capture_output=True, timeout=30)
    assert result.returncode == 2
    assert source.read_bytes() == b'original source'


# dependency mistakes fail without changing the persisted context
@pytest.mark.parametrize('deps', [['missing'], ['asset']])
def test_context_dependency_validation(tmp_path, deps):
    context = tmp_path / 'context.json'; context.write_text('{}')
    (tmp_path / 'asset.txt').write_text('evidence')
    with pytest.raises(ValueError):
        record(context, 'asset', {'path':'asset.txt','phase':'sources','summary':'source evidence','status':'draft','depends_on':deps})
    assert context.read_text() == '{}'
    assert view(context)['artifacts'] == []


# recording the context itself cannot make its fingerprint stale immediately
def test_context_self_alias(tmp_path):
    context = tmp_path / 'context.json'; context.write_text('{}')
    alias = tmp_path / 'alias.json'; os.link(context, alias)
    with pytest.raises(ValueError, match='itself'):
        record(context, 'self', {'path':'alias.json'})
    assert context.read_text() == '{}'


# exclusive evidence writes preserve old files and dangling symlinks
@pytest.mark.parametrize('link', [False, True])
def test_exclusive_json(tmp_path, link):
    out = tmp_path / 'out.json'
    if link:
        out.symlink_to(tmp_path / 'missing')
    else:
        out.write_text('old')
    with pytest.raises(FileExistsError):
        save_json(out, {'new':True}, exclusive=True)
    assert not (tmp_path / 'missing').exists()
