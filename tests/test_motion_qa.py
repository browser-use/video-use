"""Delivery errors that can remain invisible in a visually convincing export."""
import struct

from helpers.motion_qa import mp4_faststart, sample_indices, spans, validate_video


# verify delivery validation catches wrong rate and truncated frames
def test_delivery_validation_catches_wrong_rate_and_truncated_frames():
    video = {"width": 1920, "height": 1080, "avg_frame_rate": "24/1", "duration": "11.0", "codec_name": "h264", "pix_fmt": "yuv420p", "color_space": "bt709"}
    errors = validate_video(video, {"fps": 30, "duration": 12, "frameCount": 360}, 264)
    assert len(errors) == 3
    assert any("frame count" in error for error in errors)
    video.update(avg_frame_rate="30/1", duration="12.0")
    assert validate_video(video, {"fps": 30, "duration": 12, "frameCount": 360}, 360) == []


# verify contact sheet includes the actual final frame
def test_contact_sheet_includes_the_actual_final_frame():
    indices = sample_indices(360, 12)
    assert indices[0] == 0
    assert indices[-1] == 359
    assert len(set(indices)) == 12
    assert sample_indices(2, 12) == [0, 1]


# verify review cues preserve one frame boundaries and ignore short holds
def test_review_cues_preserve_one_frame_boundaries_and_ignore_short_holds():
    assert spans([0, 1, 3], 30) == [
        {"start": 0.0, "end": 0.0667, "duration": 0.0667},
        {"start": 0.1, "end": 0.1333, "duration": 0.0333},
    ]
    assert spans([0, 1, 3], 30, minimum_seconds=.75) == []


# verify faststart reads atom structure instead of searching payload
def test_faststart_reads_atom_structure_instead_of_searching_payload(tmp_path):
    atom = lambda kind, content=b"": struct.pack(">I4s", 8 + len(content), kind) + content
    video = tmp_path / "video.mp4"
    video.write_bytes(atom(b"ftyp") + atom(b"moov") + atom(b"mdat", b"payload"))
    assert mp4_faststart(video)
    video.write_bytes(atom(b"ftyp") + atom(b"mdat", b"fake moov bytes") + atom(b"moov"))
    assert not mp4_faststart(video)
    video.write_bytes(struct.pack(">I4s", 2, b"moov"))
    assert not mp4_faststart(video)

import json
import shutil
import subprocess
import pytest
from helpers.motion_qa import main


# reject invalid delivery expectations before decoding or creating artifacts
@pytest.mark.parametrize('flag,value', [('--expect-fps','nan'), ('--expect-duration','-1'), ('--expect-width','0')])
def test_invalid_expectations_fail_before_output(tmp_path, flag, value):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'original')
    with pytest.raises(SystemExit):
        main([str(source), flag, value])
    assert list(tmp_path.iterdir()) == [source]


# refuse existing review directories and links without changing their contents
def test_existing_output_directory_is_preserved(tmp_path):
    source = tmp_path / 'source.mp4'
    source.write_bytes(b'original')
    out = tmp_path / 'review'
    out.mkdir()
    (out / 'poster.png').hardlink_to(source)
    with pytest.raises(FileExistsError):
        main([str(source), '--output-dir', str(out)])
    assert source.read_bytes() == b'original'


# validate actual encoded audio video and proof images including a failed contract
@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_encoded_video_report_images_and_contract_failure(tmp_path):
    source = tmp_path / 'source.mp4'
    subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=1',
                    '-f','lavfi','-i','sine=frequency=440:duration=1','-c:v','libx264','-pix_fmt','yuv420p',
                    '-c:a','aac','-movflags','+faststart','-shortest',str(source)],check=True)
    args = [str(source),'--samples','3','--thumbnail-width','160','--expect-fps','10',
            '--expect-duration','1','--expect-audio']
    assert main(args) == 0
    reports = list(tmp_path.glob('source.qa-*/qa.json'))
    report = json.loads(reports[0].read_text())
    assert report['technicalPass']
    assert report['metrics']['decodedFrames'] == 10
    assert report['images']['sampleFrames'] == [0,4,9]
    from PIL import Image
    with Image.open(report['images']['contactSheet']) as sheet:
        assert sheet.width > 160 and sheet.height > 90
    assert main([*args,'--expect-width','320']) == 1
    assert len(list(tmp_path.glob('source.qa-*/qa.json'))) == 2
    assert json.loads(reports[0].read_text()) == report
