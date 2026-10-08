"""Offline contracts for optional motion-design slots."""
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.util.spec_from_file_location("motion_slot", ROOT / "helpers/motion_slot.py")
motion = importlib.util.module_from_spec(loader)
loader.loader.exec_module(motion)


# A valid clock retains exact frame count and optional dependencies stay local
def test_init(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_USE_MOTION_NODE_MODULES", raising=False)
    slot = motion.init_slot(tmp_path / "edit", "launch", motion.validate_spec(1280, 720, "30", 8))
    assert json.loads((slot / "motion.json").read_text())["frame_count"] == 240
    assert "__WIDTH__" not in (slot / "index.html").read_text()
    assertions = json.loads((slot / "index.motion.json").read_text())
    assert assertions["duration"] == 8
    assert [row["kind"] for row in assertions["assertions"]] == ["appearsBy", "before"]
    assert not (slot / "node_modules").exists()
    assert not (tmp_path / "edit/edl.json").exists()
    with pytest.raises(ValueError, match="already exists"):
        motion.init_slot(tmp_path / "edit", "launch", motion.validate_spec(1280, 720, "30", 8))


# Reject traversal absolute names and names capable of escaping the slot directory
@pytest.mark.parametrize("name", ["../x", "/tmp/x", "", "a/b", ".", "x y"])
def test_invalid_names(name):
    with pytest.raises(ValueError):
        motion.safe_name(name)


# Invalid media specifications fail before rendering or downloading anything
@pytest.mark.parametrize("width,height,fps,duration", [(1279,720,"30",8),(1280,0,"30",8),(1280,720,"0",8),
    (1280,720,"1/0",8),(1280,720,"nan",8),(1280,720,"30000/1001",10.01),
    (1280,720,"30",float("nan")),(1280,720,"30",.1),(1280,720,"30",1.01)])
def test_invalid_specs(width, height, fps, duration):
    with pytest.raises(ValueError):
        motion.validate_spec(width, height, fps, duration)


# Session artifacts cannot be initialized inside the framework checkout
def test_repository_output_rejected():
    with pytest.raises(ValueError, match="outside"):
        motion.init_slot(ROOT / "edit", "bad", motion.validate_spec(1280, 720, "30", 8))


# A symlink cannot redirect the animation directory into unrelated user files
def test_symlink_parent(tmp_path):
    (tmp_path / "edit").mkdir()
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "edit/animations").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        motion.init_slot(tmp_path / "edit", "bad", motion.validate_spec(1280, 720, "30", 8))


# Missing packages yield instructions not an automatic npm installation
def test_missing_dependency(tmp_path):
    with pytest.raises(ValueError, match="npm install"):
        motion.require_hyperframes(tmp_path)


# Both engine and animation library versions are part of the reproducible slot
@pytest.mark.parametrize("package,wrong",[("hyperframes","0.8.29"),("gsap","3.14.0")])
def test_dependency_version_guard(tmp_path, package, wrong):
    for name, version in [("hyperframes","0.8.30"),("gsap","3.15.0")]:
        folder = tmp_path / "node_modules" / name
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"version":wrong if name==package else version}))
    binary = tmp_path / "node_modules/.bin/hyperframes"
    binary.parent.mkdir()
    binary.touch()
    with pytest.raises(ValueError,match=f"requires {package}"):
        motion.require_hyperframes(tmp_path)


# Asset records retain content identity and reject accidental replacement
def test_asset_provenance(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_USE_MOTION_NODE_MODULES", raising=False)
    slot = motion.init_slot(tmp_path / "edit", "asset", motion.validate_spec(1280, 720, "30", 8))
    source = tmp_path / "mark.svg"
    source.write_text('<svg xmlns="http://www.w3.org/2000/svg"/>')
    record = motion.add_asset(slot, source, "https://example.org/brand", "User supplied for this project")
    assert record["sha256"] == motion.fingerprint(source)
    assert json.loads((slot / "assets.json").read_text())["assets"] == [record]
    with pytest.raises(ValueError, match="already exists"):
        motion.add_asset(slot, source, "https://example.org/brand", "User supplied")


# A failed encode must not publish a partial output or damage another file
def test_failed_render_is_not_published(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_USE_MOTION_NODE_MODULES", raising=False)
    slot = motion.init_slot(tmp_path / "edit", "fail", motion.validate_spec(1280, 720, "30", 8))
    monkeypatch.setattr(motion, "require_hyperframes", lambda slot: Path("/fake"))
    # fail
    def fail(slot, arguments):
        raise subprocess.CalledProcessError(1, arguments)
    monkeypatch.setattr(motion, "engine_command", fail)
    with pytest.raises(subprocess.CalledProcessError):
        motion.render_slot(slot)
    assert not (slot / "render.mp4").exists()
    assert not list(slot.glob(".motion-render-*"))


# Revisions must use distinct names so an existing video is never clobbered
def test_render_output_safety(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_USE_MOTION_NODE_MODULES", raising=False)
    slot = motion.init_slot(tmp_path / "edit", "safe", motion.validate_spec(1280, 720, "30", 8))
    output = slot / "render.mp4"
    output.write_bytes(b"keep")
    with pytest.raises(ValueError, match="new .mp4"):
        motion.render_slot(slot)
    assert output.read_bytes() == b"keep"


# A missing MP4 does not authorize replacing an existing verification record
def test_render_sidecar_safety(tmp_path, monkeypatch):
    monkeypatch.delenv("VIDEO_USE_MOTION_NODE_MODULES", raising=False)
    slot = motion.init_slot(tmp_path / "edit", "safe", motion.validate_spec(1280,720,"30",8))
    sidecar = slot / "render.verify.json"
    sidecar.write_text("keep")
    with pytest.raises(ValueError,match="sidecar already exists"):
        motion.render_slot(slot)
    assert sidecar.read_text() == "keep"


# Public CLI help remains usable without the optional JavaScript engine
def test_help():
    result = subprocess.run(["python3", str(ROOT / "helpers/motion_slot.py"), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "init" in result.stdout


# Encoded stream metadata must match the declared dimensions clock and frames
@pytest.mark.parametrize("field,value", [("width",1920),("avg_frame_rate","24/1"),("nb_read_frames","239")])
def test_verify_rejects_mismatched_stream(tmp_path, monkeypatch, field, value):
    stream = {"codec_type":"video","width":1280,"height":720,"avg_frame_rate":"30/1","nb_read_frames":"240"}
    stream[field] = value
    monkeypatch.setattr(motion.subprocess,"run",lambda *a,**kw: subprocess.CompletedProcess(a,0,json.dumps({"streams":[stream]}),""))
    with pytest.raises(ValueError, match="differ"):
        motion.verify_video(tmp_path / "fake.mp4",motion.validate_spec(1280,720,"30",8))


# A correct probe still requires a complete decode before a render is accepted
def test_verify_decodes_after_probe(tmp_path, monkeypatch):
    calls = []
    stream = {"codec_type":"video","width":1280,"height":720,"avg_frame_rate":"30/1","nb_read_frames":"240"}
    # run
    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,0,json.dumps({"streams":[stream]}),"")
    monkeypatch.setattr(motion.subprocess,"run",run)
    motion.verify_video(tmp_path / "fake.mp4",motion.validate_spec(1280,720,"30",8))
    assert [call[0] for call in calls] == ["ffprobe","ffmpeg"]
    assert "-xerror" in calls[1]


# callers cannot bypass the clock and engine contract by supplying an inconsistent spec
def test_init_revalidates_spec(tmp_path):
    spec = motion.validate_spec(1280,720,'30',8)
    spec['frame_count'] = 1
    with pytest.raises(ValueError,match='validated'):
        motion.init_slot(tmp_path/'edit','invalid',spec)
    assert not (tmp_path/'edit').exists()


# metadata failures remove only the newly copied asset
def test_asset_metadata_failure_rolls_back(tmp_path,monkeypatch):
    monkeypatch.delenv('VIDEO_USE_MOTION_NODE_MODULES',raising=False)
    slot=motion.init_slot(tmp_path/'edit','asset',motion.validate_spec(1280,720,'30',8))
    source=tmp_path/'shape.svg';source.write_text('<svg/>')
    before=(slot/'assets.json').read_bytes()
    # simulate a full disk while committing provenance
    def fail(*args):
        raise OSError('write failed')
    monkeypatch.setattr(motion,'atomic_json',fail)
    with pytest.raises(OSError):
        motion.add_asset(slot,source,'local fixture','original test shape')
    assert not (slot/'assets/shape.svg').exists()
    assert (slot/'assets.json').read_bytes()==before


# real encoded media is verified and a concurrently created report remains untouched
@pytest.mark.parametrize('collision',[False,True])
def test_verified_render_publication(tmp_path,monkeypatch,collision):
    monkeypatch.delenv('VIDEO_USE_MOTION_NODE_MODULES',raising=False)
    slot=motion.init_slot(tmp_path/'edit','render',motion.validate_spec(128,72,'10',1))
    monkeypatch.setattr(motion,'require_hyperframes',lambda slot:Path('/fake'))
    # substitute only the engine and exercise real probing decoding and publication
    def encode(slot,arguments):
        target=arguments[arguments.index('--output')+1]
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=128x72:r=10:d=1',
                        '-c:v','libx264','-pix_fmt','yuv420p',target],check=True)
        if collision:
            (slot/'render.verify.json').write_text('another writer')
    monkeypatch.setattr(motion,'engine_command',encode)
    if collision:
        with pytest.raises(FileExistsError):
            motion.render_slot(slot)
        assert not (slot/'render.mp4').exists()
        assert (slot/'render.verify.json').read_text()=='another writer'
    else:
        result=motion.render_slot(slot)
        report=json.loads((slot/'render.verify.json').read_text())
        assert report['sha256']==motion.fingerprint(result)
        assert report['spec']['frame_count']==10
