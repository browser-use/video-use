"""Map independent source word timestamps through an intact audio clip to output samples."""

import argparse
from pathlib import Path
from edit_clock import seconds_to_sample
from edit_io import load_json, save_json


# translate intact source words to the output audio sample clock
def map_words(transcript, clip, source_id, prefix="w"):
    for key in ("source_start_sample", "start_sample", "sample_count"):
        if type(clip.get(key)) is not int or clip[key] < (
            1 if key == "sample_count" else 0
        ):
            raise ValueError(
                "clip needs integer sample offsets and a positive sample count"
            )
    start = clip["source_start_sample"]
    end = start + clip["sample_count"]
    result = {}
    for i, word in enumerate(transcript["words"]):
        if word.get("type", "word") != "word":
            continue
        if float(word["start"]) < 0 or float(word["end"]) <= float(word["start"]):
            raise ValueError("word timestamps must describe a positive nonnegative interval")
        a = seconds_to_sample(word["start"])
        b = seconds_to_sample(word["end"])
        if a < 0 or b <= a:
            raise ValueError(
                "word timestamps must describe a positive nonnegative interval"
            )
        if b <= start or a >= end:
            continue
        if a < start or b > end:
            raise ValueError(
                f'audio trim cuts through word {word["text"]!r}; extend source handles'
            )
        result[f"{prefix}{i}"] = {
            "text": word["text"],
            "source": source_id,
            "source_start_sample": a,
            "source_end_sample": b,
            "start_sample": clip["start_sample"] + a - start,
            "end_sample": clip["start_sample"] + b - start,
            "audio_clip": clip["id"],
        }
    return result


# sequence comparison of final asr vs plan  edits and onset differences stay visible
def compare_words(planned, observed, intervals=None):
    """Sequence comparison of final ASR vs plan; edits and onset differences stay visible."""
    from difflib import SequenceMatcher
    import re

    clean = lambda t: re.sub(r"[^\w]", "", t).casefold()
    left = sorted(planned.values(), key=lambda w: w["start_sample"])
    right = [w for w in observed["words"] if w.get("type", "word") == "word"]
    if intervals is not None:
        right = [
            w
            for w in right
            if any(a <= seconds_to_sample(w["start"]) < b for a, b in intervals)
        ]
    rows = []
    for op, a, b, c, d in SequenceMatcher(
        None,
        [clean(w["text"]) for w in left],
        [clean(w["text"]) for w in right],
        autojunk=False,
    ).get_opcodes():
        if op == "equal":
            rows.extend(
                {
                    "operation": "match",
                    "text": p["text"],
                    "onset_delta_ms": (
                        seconds_to_sample(o["start"]) - p["start_sample"]
                    )
                    / 48,
                }
                for p, o in zip(left[a:b], right[c:d])
            )
        else:
            rows.append(
                {
                    "operation": op,
                    "planned": [w["text"] for w in left[a:b]],
                    "heard": [w["text"] for w in right[c:d]],
                }
            )
    return {
        "rows": rows,
        "limit": "ASR disagreement is a review candidate, not automatic proof of an edit error. Verify against the real recording.",
    }


# map or compare transcript evidence without overwriting input documents
def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map")
    m.add_argument("transcript")
    m.add_argument("clip")
    m.add_argument("--source", required=True)
    m.add_argument("--prefix", default="w")
    m.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("manifest")
    c.add_argument("final_asr")
    c.add_argument("--out", required=True)
    a = p.parse_args()
    inputs = [a.transcript, a.clip] if a.cmd == "map" else [a.manifest, a.final_asr]
    output = Path(a.out).resolve()
    if any(
        output == Path(path).resolve()
        or (output.exists() and Path(path).exists() and output.samefile(path))
        for path in inputs
    ):
        p.error("output would overwrite an input")
    if a.cmd == "map":
        result = map_words(
            load_json(a.transcript), load_json(a.clip), a.source, a.prefix
        )
    else:
        manifest = load_json(a.manifest)
        intervals = [
            (c["start_sample"], c["start_sample"] + c["sample_count"])
            for c in manifest.get("audio", [])
            if c["role"] == "voice"
        ]
        result = compare_words(manifest["words"], load_json(a.final_asr), intervals)
    save_json(a.out, result, exclusive=True)


if __name__ == "__main__":
    main()
