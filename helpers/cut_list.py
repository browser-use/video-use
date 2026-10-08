"""Compile an editor's chosen shots and audit frame/caption/provenance contracts."""

import argparse
import math
from pathlib import Path
from edit_clock import allocate_frames, check_partition, frame_to_sample
from edit_io import load_json, save_json, source_path


# reject unsupported fields instead of discarding requested behavior
def known(row, allowed, where):
    extra = set(row) - set(allowed.split())
    if extra:
        raise ValueError(
            f"{where}: unsupported fields {sorted(extra)}; do not silently discard requested treatments"
        )


# assign contiguous frame intervals to the chosen shot sequence
def compile_shots(rows, total=None):
    counts = (
        allocate_frames(total, [r["weight"] for r in rows])
        if total is not None
        else [r["frames"] for r in rows]
    )
    output = []
    cursor = 0
    for i, (row, n) in enumerate(zip(rows, counts)):
        if type(n) is not int or n <= 0:
            raise ValueError("frames must be positive integers")
        item = {k: v for k, v in row.items() if k not in ("weight", "frames")}
        item.update(
            id=item.get("id", f"shot_{i+1:02}"),
            start_frame=cursor,
            end_frame=cursor + n,
        )
        output.append(item)
        cursor += n
    return output


# check composition geometry timing provenance and supported fields
def validate(manifest, root, check_files=True):
    # reject nonfinite numeric values throughout the manifest
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("numeric fields must be finite")
        if isinstance(value, dict):
            for item in value.values():
                finite(item)
        elif isinstance(value, list):
            for item in value:
                finite(item)

    finite(manifest)
    known(
        manifest,
        "version fps total_frames canvas picture font_layout fonts sources study_media shots words audio cards layers music_required_intervals delivery notes",
        "manifest",
    )
    if manifest.get("version") not in (3, "music-story-edit/1"):
        raise ValueError("unsupported manifest version")
    if manifest.get("fps") != 30:
        raise ValueError("this tested renderer requires output fps=30")
    total = manifest["total_frames"]
    check_partition(manifest["shots"], total)
    if type(total) is not int or total <= 0:
        raise ValueError("total_frames must be a positive integer")
    width, height = manifest["canvas"]
    x, y, w, h = manifest["picture"]
    if any(type(v) is not int or v % 2 for v in (width, height, x, y, w, h)):
        raise ValueError("canvas and picture geometry must be even integer pixels")
    if (
        min(width, height, w, h) <= 0
        or min(x, y) < 0
        or x + w > width
        or y + h > height
    ):
        raise ValueError("picture must fit inside canvas")
    words = manifest.get("words", {})
    if manifest.get("font_layout", "basic") not in ("basic", "raqm"):
        raise ValueError("unsupported font layout")
    shot_ids = set()
    for shot in manifest["shots"]:
        known(
            shot,
            "id source source_start source_frame source_crop speed fit grade isolation_mask start_frame end_frame beat reason effects time_map",
            "shot",
        )
        if shot["id"] in shot_ids:
            raise ValueError("duplicate shot id")
        shot_ids.add(shot["id"])
        if shot.get("source") is not None:
            source = manifest["sources"].get(shot["source"])
            if not source or source.get("study_only") or not source.get("provenance"):
                raise ValueError(
                    "shot needs an independently sourced render source with provenance"
                )
        if not shot.get("beat") or not shot.get("reason"):
            raise ValueError("each shot needs its story beat and selection reason")
        if shot.get("source") is not None and check_files:
            source_path(manifest, root, shot["source"])
        from effects import validate_source_window

        validate_source_window(shot)
        if shot.get("grade"):
            known(shot["grade"], "contrast saturation gamma", "grade")
        from effects import validate_effects, validate_time_map

        validate_effects(shot.get("effects", {}))
        if "isolation_mask" in shot:
            from cards import validate_mask

            validate_mask(
                shot["isolation_mask"],
                shot["start_frame"],
                shot["end_frame"],
                (w, h),
                root,
                check_files,
            )
        if shot.get("time_map") is not None:
            validate_time_map(shot["time_map"], shot["end_frame"] - shot["start_frame"])
            if shot.get("source") is None or any(
                k in shot for k in ("source_frame", "source_start")
            ):
                raise ValueError(
                    "time_map requires a source and already defines its source origins"
                )
            if (
                shot.get("grade")
                or shot.get("isolation_mask")
                or shot.get("speed", 1) != 1
            ):
                raise ValueError(
                    "time_map with grade/isolation/speed needs a prepared source; use a masked foreground layer instead"
                )
    sample_total = frame_to_sample(total)
    audio_ids = set()
    for clip in manifest.get("audio", []):
        known(
            clip,
            "id source role start_sample source_start_sample sample_count gain_db gain_points fade_in_samples fade_out_samples filters",
            "audio clip",
        )
        if clip["id"] in audio_ids:
            raise ValueError("duplicate audio id")
        audio_ids.add(clip["id"])
        for key in ("start_sample", "source_start_sample", "sample_count"):
            if type(clip[key]) is not int or clip[key] < 0:
                raise ValueError(f"{key} must be nonnegative integer")
        if (
            clip["sample_count"] <= 0
            or clip["start_sample"] + clip["sample_count"] > sample_total
        ):
            raise ValueError("audio clip exceeds output timeline")
        if clip["role"] not in ("voice", "music", "effects"):
            raise ValueError("unknown audio role")
        if check_files:
            source_path(manifest, root, clip["source"])
        from mix_audio import filter_chain, gain_envelope

        filter_chain(clip.get("filters", []))
        gain_envelope(clip["sample_count"], clip.get("gain_points", []), clip.get("gain_db", 0))
    for ident, word in words.items():
        known(word, "text source source_start_sample source_end_sample start_sample end_sample audio_clip", "word")
        if word.get("source") not in manifest["sources"]:
            raise ValueError(f"{ident}: missing word source")
        for key in (
            "start_sample",
            "end_sample",
            "source_start_sample",
            "source_end_sample",
        ):
            if type(word.get(key)) is not int or word[key] < 0:
                raise ValueError(f"{ident}: word needs integer {key}")
        if (
            not word["start_sample"] < word["end_sample"] <= sample_total
            or word["source_start_sample"] >= word["source_end_sample"]
        ):
            raise ValueError("invalid word interval")
        if word.get("audio_clip"):
            clip = next(
                (c for c in manifest.get("audio", []) if c["id"] == word["audio_clip"]),
                None,
            )
            if not clip or clip["source"] != word["source"] or clip["role"] != "voice":
                raise ValueError("word does not map to its voice clip")
            offset = clip["start_sample"] - clip["source_start_sample"]
            if (
                word["start_sample"] != word["source_start_sample"] + offset
                or word["end_sample"] != word["source_end_sample"] + offset
            ):
                raise ValueError("word mapping disagrees with source audio clock")
    card_ids = set()
    for card in manifest.get("cards", []):
        known(
            card,
            "id start_frame end_frame word_ids display_normalization timing_exception animation occlusion_mask shadow outline lines",
            "card",
        )
        if card["id"] in card_ids:
            raise ValueError("duplicate card id")
        card_ids.add(card["id"])
        a, b = card["start_frame"], card["end_frame"]
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= total:
            raise ValueError("invalid card interval")
        if not card.get("word_ids") or any(i not in words for i in card["word_ids"]):
            raise ValueError("every card must link to source-backed word IDs")
        if any(not words[i].get("source") for i in card["word_ids"]):
            raise ValueError("word provenance is missing")
        if any(not line.get("text", "").strip() for line in card["lines"]):
            raise ValueError("empty caption line")
        if not card["lines"]:
            raise ValueError("empty caption card")
        for line in card["lines"]:
            known(
                line,
                "text font x y cap_height max_width fit ink_size color gradient curve",
                "caption line",
            )
            if line["font"] not in manifest["fonts"]:
                raise ValueError("unknown caption font")
            if line.get("fit", "natural") not in ("natural", "ink_box"):
                raise ValueError("unsupported typography fit")
            if float(line["cap_height"]) <= 0:
                raise ValueError("invalid cap height")
            if line.get("curve"):
                known(
                    line["curve"],
                    "kind bend_ratio angle_degrees direction",
                    "caption curve",
                )
            if line.get("gradient"):
                known(line["gradient"], "to", "caption gradient")
        if card.get("animation"):
            known(
                card["animation"],
                "entry_frames scale_from blur_from blur_to easing power",
                "caption animation",
            )
        if "occlusion_mask" in card:
            from cards import validate_mask

            validate_mask(card["occlusion_mask"], a, b, (w, h), root, check_files)
        # Alternate display spelling requires an explicit explanation; timing is audited separately.
        import re

        norm = lambda s: re.sub(r"[^\w]", "", s).casefold()
        spoken = "".join(norm(words[i]["text"]) for i in card["word_ids"])
        shown = "".join(norm(line["text"]) for line in card["lines"])
        if spoken != shown and not card.get("display_normalization"):
            raise ValueError(
                f'card {card["id"]}: displayed wording differs from source words'
            )
    for span in manifest.get("music_required_intervals", []):
        known(span, "start_frame end_frame minimum_rms_dbfs", "music presence interval")
        if not 0 <= span["start_frame"] < span["end_frame"] <= total:
            raise ValueError("invalid music interval")
    if manifest.get("delivery"):
        known(
            manifest["delivery"],
            "lufs true_peak lufs_tolerance max_final_true_peak",
            "delivery",
        )
    from effects import validate_layers

    validate_layers(manifest, root, check_files)
    if check_files:
        from edit_io import probe

        for ident, source in manifest["sources"].items():
            if source.get("study_only"):
                continue
            for stream in probe(source_path(manifest, root, ident))["streams"]:
                if stream.get("color_transfer") in ("smpte2084", "arib-std-b67"):
                    raise ValueError(
                        "HDR source needs explicit prepare_source.py --tonemap before SDR composition"
                    )
    return {
        "valid": True,
        "shots": len(manifest["shots"]),
        "frames": total,
        "audio_clips": len(manifest.get("audio", [])),
        "cards": len(manifest.get("cards", [])),
        "limit": "Structural validation does not prove source identity or creative quality.",
    }


# parse arguments and write new output artifacts without replacing existing files
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input")
    p.add_argument("--compile", action="store_true")
    p.add_argument("--total", type=int)
    p.add_argument("--out")
    a = p.parse_args()
    if a.out and (Path(a.out).exists() or Path(a.out).is_symlink()):
        raise FileExistsError("output must be a new file")
    data = load_json(a.input)
    result = (
        compile_shots(data, a.total)
        if a.compile
        else validate(data, Path(a.input).resolve().parent)
    )
    if a.out:
        save_json(a.out, result)
    else:
        print(result)


if __name__ == "__main__":
    main()
