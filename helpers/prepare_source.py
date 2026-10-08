"""Create an explicit lossless source derivative with optional HDR to SDR conversion."""

import argparse
from pathlib import Path
from edit_io import probe, run, save_json, sha256


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
    before = probe(source)
    video = next(s for s in before["streams"] if s["codec_type"] == "video")
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
            or crop[0] + crop[2] > video["width"]
            or crop[1] + crop[3] > video["height"]
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
    out.parent.mkdir(parents=True, exist_ok=True)
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-n",
        "-threads",
        "2",
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
    run(args + [out], log=Path(str(out) + ".log"))
    result = {
        "source": str(source),
        "source_sha256": sha256(source),
        "output": str(out),
        "output_sha256": sha256(out),
        "filters": filters,
        "input_probe": before,
        "output_probe": probe(out, True),
        "review": "Compare native frames for color and crop; reacquire if source quality is inadequate",
    }
    save_json(str(out) + ".json", result, exclusive=True)
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
