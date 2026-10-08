"""Create an explicit lossless source derivative with optional HDR to SDR conversion."""

import argparse
import math
import os
from pathlib import Path
import tempfile
from edit_io import display_dimensions, file_state, probe, run, save_json, sha256
from source_scan import timestamps


# create a new FFV1 working copy and record its explicit transformations
def prepare(source, out, crop=None, tonemap=False):
    """Create a new FFV1 working copy and record its explicit transformations."""
    source = Path(source).resolve()
    out = Path(out).absolute()
    outputs = [out, Path(str(out) + ".log"), Path(str(out) + ".json")]
    if any(p.exists() or p.is_symlink() for p in outputs) or source == out:
        raise FileExistsError("choose a new source derivative path")
    if out.suffix.lower() != ".mkv":
        raise ValueError("lossless prepared sources use a Matroska mkv file")
    state = file_state(source)
    digest = sha256(source)
    before = probe(source)
    video = next((s for s in before["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise ValueError("prepared sources require a video stream")
    width, height = display_dimensions(video)
    hdr = video.get("color_transfer") in ("smpte2084", "arib-std-b67")
    if hdr and not tonemap:
        raise ValueError("HDR source requires an explicit tonemap decision")
    if tonemap and not hdr:
        raise ValueError("tonemap requires tagged PQ or HLG input")
    filters = []
    if crop is not None:
        if (
            len(crop) != 4
            or any(type(v) is not int or v % 2 for v in crop)
            or min(crop[:2]) < 0
            or min(crop[2:]) <= 0
            or crop[0] + crop[2] > width
            or crop[1] + crop[3] > height
        ):
            raise ValueError("crop must be an even pixel rectangle inside source")
        x, y, w, h = crop
        filters.append(f"crop={w}:{h}:{x}:{y}")
    if tonemap:
        filters += [
            "zscale=transfer=linear:npl=100",
            "format=gbrpf32le",
            "zscale=primaries=bt709",
            "tonemap=tonemap=mobius:desat=0",
            "zscale=transfer=bt709:matrix=bt709:range=limited",
        ]
    original_pts = timestamps(source)
    if file_state(source) != state:
        raise ValueError("source changed before preparation")
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-n",
        "-threads",
        "2",
        "-nostdin",
        "-xerror",
        "-copyts",
        "-i",
        source,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-sn",
        "-dn",
    ]
    if filters:
        args += ["-filter_threads", "1", "-vf", ",".join(filters)]
    args += [
        "-fps_mode",
        "passthrough",
        "-c:v",
        "ffv1",
        "-pix_fmt",
        "yuv444p10le",
        "-c:a",
        "pcm_s24le",
    ]
    if tonemap:
        args += [
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
        ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".prepare-", dir=out.parent) as folder:
        staged = Path(folder) / out.name
        log, record = Path(folder) / "command.log", Path(folder) / "record.json"
        run(args + [staged], log=log)
        prepared_pts = timestamps(staged)
        if len(prepared_pts) != len(original_pts) or any(
            not math.isclose(a, b, rel_tol=0, abs_tol=0.001) for a, b in zip(original_pts, prepared_pts)
        ):
            raise ValueError("prepared copy changed native video timestamps")
        after = probe(staged, True)
        if file_state(source) != state or sha256(source) != digest or file_state(source) != state:
            raise ValueError("source changed during preparation")
        result = {
            "source": str(source),
            "source_sha256": digest,
            "output": str(out),
            "output_sha256": sha256(staged),
            "filters": filters,
            "input_probe": before,
            "output_probe": after,
            "review": "Compare native frames for color and crop; reacquire if source quality is inadequate",
        }
        save_json(record, result, exclusive=True)
        published = []
        try:
            # Exclusive links publish complete files; expose the movie last.
            for source_file, destination in ((log, outputs[1]), (record, outputs[2]), (staged, out)):
                os.link(source_file, destination)
                published.append((source_file, destination))
        except BaseException:
            for source_file, destination in reversed(published):
                if destination.exists() and destination.samefile(source_file):
                    destination.unlink()
            raise
    return result


# parse the crop and HDR conversion choices for a new working copy
def main():
    """Parse the crop and HDR conversion choices for a new working copy."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--crop", nargs=4, type=int)
    p.add_argument("--tonemap", action="store_true")
    a = p.parse_args()
    prepare(a.source, a.out, a.crop, a.tonemap)


if __name__ == "__main__":
    main()
