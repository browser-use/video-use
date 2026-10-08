"""Verify that execution snapshots track core source without local applications."""
import pytest
from helpers.runtime_snapshot import fingerprint, main, verify_snapshot


# source edits fail before a worker can use a stale toolkit
def test_snapshot_rejects_changed_missing_and_added_sources(tmp_path):
    folder = tmp_path / "helpers"
    folder.mkdir()
    source = folder / "render.py"
    source.write_text("original")
    expected = fingerprint(tmp_path)
    assert verify_snapshot(expected, tmp_path) == expected
    source.write_text("changed")
    with pytest.raises(RuntimeError, match="helpers/render.py"):
        verify_snapshot(expected, tmp_path)
    source.unlink()
    with pytest.raises(RuntimeError, match="helpers/render.py"):
        verify_snapshot(expected, tmp_path)
    source.write_text("original")
    (folder / "new.py").write_text("new")
    with pytest.raises(RuntimeError, match="helpers/new.py"):
        verify_snapshot(expected, tmp_path)


# credentials and generated caches never enter snapshot evidence
def test_snapshot_excludes_private_and_generated_files(tmp_path):
    (tmp_path / "helpers").mkdir()
    (tmp_path / "helpers" / "a.py").write_text("source")
    expected = fingerprint(tmp_path)
    (tmp_path / ".env").write_text("private")
    (tmp_path / "helpers" / ".env.local").write_text("private")
    cache = tmp_path / "helpers" / "__pycache__"
    cache.mkdir()
    (cache / "a.pyc").write_bytes(b"cache")
    assert fingerprint(tmp_path) == expected


# Separate applications and local run artifacts never change the core identity
def test_snapshot_ignores_files_outside_shipped_core(tmp_path):
    (tmp_path / "helpers").mkdir()
    (tmp_path / "helpers" / "render.py").write_text("source")
    expected = fingerprint(tmp_path)
    for name in ("gui", "benchmarks", "experiments", "website", "observer"):
        folder = tmp_path / name
        folder.mkdir()
        (folder / "local.py").write_text("local application")
    assert fingerprint(tmp_path) == expected



# round trip the command line and preserve an existing record
def test_record_check_and_existing_output(tmp_path):
    (tmp_path / 'helpers').mkdir()
    source = tmp_path / 'helpers/render.py'
    source.write_text('original')
    output = tmp_path / 'edit/tool-record.json'
    args = ['--root', str(tmp_path)]
    assert main([*args, 'record', '-o', str(output)]) == 0
    original = output.read_bytes()
    assert main([*args, 'check', str(output)]) == 0
    assert main([*args, 'record', '-o', str(output)]) == 1
    assert output.read_bytes() == original
    source.write_text('changed')
    assert main([*args, 'check', str(output)]) == 1


# reject empty roots and records that would change their own inputs
def test_invalid_root_and_self_record(tmp_path):
    assert main(['--root', str(tmp_path), 'record', '-o', str(tmp_path/'record.json')]) == 1
    assert main(['--root', str(tmp_path), 'record', '-o', str(tmp_path/'helpers/record.json')]) == 1
    assert not (tmp_path/'helpers').exists()


# keep symbolic links and nested environment directories out of the record
def test_links_and_environment_directories_are_ignored(tmp_path):
    helpers = tmp_path/'helpers'
    helpers.mkdir()
    (helpers/'source.py').write_text('source')
    expected = fingerprint(tmp_path)
    private = tmp_path/'private.txt'
    private.write_text('private')
    (helpers/'linked.py').symlink_to(private)
    (helpers/'.env.backup').mkdir()
    (helpers/'.env.backup'/'key').write_text('private')
    assert fingerprint(tmp_path) == expected


# reject edited records before comparing the current tool copy
def test_invalid_record_checksum(tmp_path):
    with pytest.raises(ValueError, match='checksum'):
        verify_snapshot({'files': {'helpers/render.py': 'wrong'}, 'sha256': 'wrong'}, tmp_path)
