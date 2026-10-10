"""Convert whisper.cpp --output-json JSON into the Scribe-shaped word list
that helpers/pack_transcripts.py expects.

whisper.cpp emits {"transcription": [{"offsets": {"from": ms, "to": ms},
"text": " word"}]} — a flat segment list with no speaker labels and no
explicit spacing entries. pack_transcripts.py expects Scribe's shape:
{"words": [{"type": "word"|"spacing", "text": ..., "start": s, "end": s,
"speaker_id": ...}]}. Feeding whisper JSON straight into the packer yields
"_no speech detected_" — a silent failure, not an error.

This adapter bridges the two and synthesizes the 'spacing' entries the
packer uses to detect silences, so phrase grouping still works.

Usage:
    python helpers/whisper_to_scribe.py transcript.json
    python helpers/whisper_to_scribe.py transcript.json -o words.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def convert(whisper: dict, silence_threshold: float = 0.5) -> dict:
    """Map whisper.cpp transcription segments onto Scribe's word schema."""
    words: list[dict] = []

    for seg in whisper.get("transcription", []):
        offsets = seg.get("offsets") or {}
        text = (seg.get("text") or "").strip()
        if not text:
            # whisper emits blank padding segments with zeroed offsets.
            # Dropping them early keeps them from poisoning prev_end below.
            continue
        start = offsets.get("from", 0) / 1000.0
        end = offsets.get("to", start * 1000) / 1000.0

        # whisper has no explicit silence markers. Emit a 'spacing' entry
        # whenever the gap since the previous word exceeds the threshold,
        # which is exactly the signal the packer flushes phrases on.
        if words and start - words[-1]["end"] >= silence_threshold:
            words.append({
                "type": "spacing",
                "text": " ",
                "start": words[-1]["end"],
                "end": start,
                "speaker_id": None,
            })

        words.append({
            "type": "word",
            "text": text,
            "start": start,
            "end": end,
            # whisper.cpp does not diarize; leave unset rather than invent one.
            "speaker_id": None,
        })

    return {"words": words}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path, help="whisper.cpp --output-json file")
    ap.add_argument("-o", "--output", type=Path, help="defaults to <input stem>.words.json")
    ap.add_argument("--silence-threshold", type=float, default=0.5)
    args = ap.parse_args()

    whisper = json.loads(args.input.read_text())
    if "transcription" not in whisper:
        sys.exit(
            f"{args.input} does not look like whisper.cpp JSON "
            "(no 'transcription' key). Pass the file written by -oj/--output-json."
        )

    out_path = args.output or args.input.with_suffix(".words.json")
    out_path.write_text(json.dumps(convert(whisper, args.silence_threshold), indent=2))
    print(f"{len(convert(whisper, args.silence_threshold)['words'])} words → {out_path}")


if __name__ == "__main__":
    main()
