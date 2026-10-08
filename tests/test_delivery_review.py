"""Regression coverage for preservation and validation of legacy deliveries."""
from pathlib import Path
import pytest
import render
from captions import write_substation, _words_from_char_alignment
from edl import _dimensions, EDLValidationError


# completed and dangling output names must remain untouched
@pytest.mark.parametrize('link', [False, True])
def test_caption_existing_output(tmp_path, link):
    path = tmp_path / 'captions.ass'
    if link:
        path.symlink_to(tmp_path / 'missing')
    else:
        path.write_text('prior captions')
    with pytest.raises(FileExistsError):
        write_substation([(0, 1, 'new')], path)
    assert not (tmp_path / 'missing').exists()
    if not link:
        assert path.read_text() == 'prior captions'


# a failed encoder cannot leave a misleading final deliverable
@pytest.mark.parametrize('success', [False, True])
def test_staged_delivery(tmp_path, monkeypatch, success):
    output = tmp_path / 'final.mp4'
    # simulate delivery without invoking a media encoder
    def encode(*, out_path, **kwargs):
        out_path.write_bytes(b'video')
        if not success:
            raise RuntimeError('encoder failed')
    monkeypatch.setattr(render, '_render_one_output', encode)
    if success:
        render.render_one_output(out_path=output)
        assert output.read_bytes() == b'video'
    else:
        with pytest.raises(RuntimeError):
            render.render_one_output(out_path=output)
        assert not output.exists()
    assert not list(tmp_path.glob('.delivery-*'))


# a concurrent writer wins without losing its file
def test_delivery_publication_race(tmp_path, monkeypatch):
    output = tmp_path / 'final.mp4'
    # simulate delivery without invoking a media encoder
    def encode(*, out_path, **kwargs):
        out_path.write_bytes(b'new')
        output.write_bytes(b'other writer')
    monkeypatch.setattr(render, '_render_one_output', encode)
    with pytest.raises(FileExistsError):
        render.render_one_output(out_path=output)
    assert output.read_bytes() == b'other writer'


# invalid timestamps and dimensions fail before expensive work
@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1])
def test_bad_character_time(value):
    with pytest.raises(ValueError):
        _words_from_char_alignment({'characters':['a'], 'character_start_times_seconds':[value], 'character_end_times_seconds':[1]})


# fractional output dimensions cannot be silently truncated
def test_fractional_dimensions():
    with pytest.raises(EDLValidationError):
        _dimensions({'width':320.5,'height':180}, 'test')

