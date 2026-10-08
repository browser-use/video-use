"""Regression cases from the source helper review."""
import json
import io
import os
import subprocess
import sys
from pathlib import Path
import pytest
from edit_clock import allocate_frames, ass_stamp, check_partition, frame_to_sample, seconds_to_frame, seconds_to_sample
from edit_io import last_json, load_json, run, save_json, sha256
from project_state import record, validate, view


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
    if helper == 'find_shot.py':
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


def test_json_utf8_and_nonexclusive_symlink_safety(tmp_path):
    original, alias = tmp_path / 'original.json', tmp_path / 'alias.json'
    original.write_bytes('{"text":"café 🌊"}'.encode('utf-8'))
    assert load_json(original)['text'] == 'café 🌊'
    alias.symlink_to(original)
    with pytest.raises(FileExistsError, match='symbolic link'):
        save_json(alias, {'text': 'changed'})
    assert load_json(original)['text'] == 'café 🌊'
    save_json(original, {'text': 'é 🌊'})
    assert 'é 🌊' in original.read_bytes().decode('utf-8')


@pytest.mark.parametrize('total', [True, 1.0, -1])
def test_partition_requires_integer_frame_total(total):
    with pytest.raises(ValueError, match='nonnegative integer'):
        check_partition([{'start_frame': 0, 'end_frame': 1}], total)


def test_ass_rejects_negative_frame():
    with pytest.raises(ValueError, match='nonnegative'):
        ass_stamp(-1)


@pytest.mark.parametrize('field,value', [
    ('path', []), ('path', '/absolute/artifact'), ('summary', 4), ('phase', []),
    ('depends_on', 'item'), ('depends_on', {}), ('depends_on', [None]),
    ('dependency_hashes', []),
])
def test_context_rejects_malformed_rows(field, value):
    row = {'phase': 'sources', 'status': 'draft', 'path': 'input.txt', 'summary': 'evidence', 'sha256': '0' * 64}
    row[field] = value
    with pytest.raises(ValueError):
        validate({'artifacts': {'item': row}})


@pytest.mark.parametrize('data', [[], {'artifacts': []}, {'artifacts': {'item': None}}])
def test_context_rejects_invalid_object_shapes(data):
    with pytest.raises(ValueError):
        validate(data)


def test_record_rejects_absolute_artifact_path(tmp_path):
    artifact = tmp_path / 'evidence.txt'
    artifact.write_text('evidence')
    with pytest.raises(ValueError, match='relative'):
        record(tmp_path / 'context.json', 'item', {'path': str(artifact)})
    assert not (tmp_path / 'context.json').exists()


def test_command_streams_errors_and_has_timeout(tmp_path):
    log = tmp_path / 'command.log'
    result = run([sys.executable, '-c', "import sys; sys.stderr.write('x' * 1000000); print('done')"], log=log)
    assert result.stdout.strip() == b'done'
    assert log.stat().st_size == 1000000 and len(result.stderr) == 6000
    with pytest.raises(RuntimeError, match='exceeded'):
        run([sys.executable, '-c', 'import time; time.sleep(3)'], timeout=0.1)


def test_duplicate_timestamps_are_valid_but_backwards_are_not(monkeypatch):
    import source_scan
    pts = ['0.0', '0.0', '0.2']

    def frames(args):
        return subprocess.CompletedProcess(args, 0, json.dumps({'frames': [{'best_effort_timestamp_time': t} for t in pts]}).encode())

    monkeypatch.setattr(source_scan, 'run', frames)
    assert source_scan.timestamps('unused') == [0, 0, 0.2]
    pts[:] = ['0.2', '0.1']
    with pytest.raises(ValueError, match='nondecreasing'):
        source_scan.timestamps('unused')


def test_known_frame_bounds_fail_before_decoder(monkeypatch):
    import source_scan
    monkeypatch.setattr(source_scan, 'probe', lambda path: {'streams': [{'codec_type': 'video', 'width': 1, 'height': 1, 'nb_frames': '30'}]})
    monkeypatch.setattr(source_scan.subprocess, 'Popen', lambda *a, **kw: pytest.fail('invalid request must not launch decoder'))
    with pytest.raises(ValueError, match='outside'):
        list(source_scan.selected_frames('unused', [30]))


def test_decoder_error_after_complete_frame_is_reported(monkeypatch):
    import source_scan
    monkeypatch.setattr(source_scan, 'probe', lambda path: {'streams': [{'codec_type': 'video', 'width': 1, 'height': 1}]})

    class FailedDecoder:
        stdout = io.BytesIO(b'\x00' * 3)

        def poll(self):
            return 1

        def wait(self):
            return 1

    monkeypatch.setattr(source_scan.subprocess, 'Popen', lambda *args, **kwargs: FailedDecoder())
    with pytest.raises(ValueError, match='decoding failed'):
        list(source_scan.selected_frames('unused', [0]))


def test_large_irregular_selection_uses_short_command_and_can_close_early(monkeypatch):
    import source_scan
    monkeypatch.setattr(source_scan, 'probe', lambda path: {'streams': [{'codec_type': 'video', 'width': 1, 'height': 1}]})
    stopped = []

    class Decoder:
        stdout = io.BytesIO(b'\x00' * 3)

        def poll(self):
            return 0 if stopped else None

        def kill(self):
            stopped.append(True)

        def wait(self):
            return 0

    def launch(args, **kwargs):
        assert len(' '.join(args)) < 4096
        script = Path(args[args.index('-filter_script:v') + 1])
        assert script.stat().st_size > 200000
        return Decoder()

    monkeypatch.setattr(source_scan.subprocess, 'Popen', launch)
    frames = source_scan.selected_frames('unused', [i * i for i in range(20000)])
    assert next(frames)[0] == 0
    frames.close()
    assert stopped


def test_search_rejects_boolean_interval():
    pytest.importorskip('cv2')
    from find_shot import search
    with pytest.raises(ValueError, match='sampling interval'):
        search('unused', 'unused', every=True)
