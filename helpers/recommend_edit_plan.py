"""Recommend an edit_plan.json from script.srt + source transcript.

Pipeline position:
    script.srt + transcript.json
      --(this script)-->
    edit_plan.json + edit_plan_review.md
      --(srt_driven_edit.py)-->
    final.mp4

Matching is best-effort LEXICAL (no LLM, no semantic understanding):
    1. Parse Scribe JSON → keep only timestamped 'word' tokens. Without
       word-level start/end timestamps we cannot produce reliable
       source_start / source_end, so plain-text transcripts are not usable.
    2. Build candidate ranges by breaking on sentence-end punctuation,
       silences ≥ gap_threshold, or speaker change; split long candidates
       at phrase punctuation then by hard word-level windows.
    3. For each SRT cue, score every candidate by:
         0.6 * SequenceMatcher(normalized chars)
         + 0.4 * Jaccard (token-level for Latin / 2-gram for CJK)
         blended with duration similarity at 0.7 / 0.3.
       The matcher cannot understand storyline — if the SRT narration uses
       words not present in the source transcript, scores will be low and
       matches will need manual review.
    4. Greedy assignment, no reuse unless --allow-reuse.
    5. Emit Form-A or Form-B plan + a sidecar review markdown.

Reserved CLI flags (placeholders, not yet wired up):
  --packed         takes_packed.md input  (use --transcript for now)
  --context-window padding around matched ranges

Usage:
    python helpers/recommend_edit_plan.py \\
      --script script.srt \\
      --transcript edit/transcripts/source.json \\
      --source source.mp4 \\
      -o edit_plan.json
    python helpers/srt_driven_edit.py \\
      --source source.mp4 --srt script.srt --plan edit_plan.json -o final.mp4
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

try:
    from srt_driven_edit import (
        parse_srt as _parse_srt,
        format_srt_ts,
        CJK_RE,
        SrtCue,  # only for type hints
    )
except Exception as e:
    raise SystemExit(
        "recommend_edit_plan: failed to import from srt_driven_edit.py. "
        f"Both files must be importable from the same helpers/ dir. ({e})"
    )


# ============================================================================
# Candidate parsing
# ============================================================================


SENT_END_PUNCT = set(".?!。？！")
PHRASE_PUNCT = set(",;:，；：、")


@dataclass
class Candidate:
    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return self.end - self.start


def load_transcript_words(path: Path, keep_audio_events: bool = False) -> list[dict]:
    """Return Scribe word tokens with valid timestamps. Optionally keep audio events."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"transcript not valid JSON: {path}: {e}")
    words = data.get("words")
    if not isinstance(words, list):
        raise SystemExit(f"transcript missing 'words' list: {path}")
    out: list[dict] = []
    for w in words:
        wt = w.get("type")
        if wt == "word":
            if w.get("start") is None or w.get("end") is None:
                continue
            out.append(w)
        elif wt == "audio_event" and keep_audio_events:
            out.append(w)
    if not out:
        raise SystemExit(f"transcript has no usable word tokens: {path}")
    return out


def _join_words(words: list[dict]) -> str:
    """Concatenate word texts. Single space between. CJK joiners are removed
    again at normalize time so this is safe even when neighbors are Chinese."""
    return " ".join((w.get("text") or "").strip() for w in words if (w.get("text") or "").strip())


def _hard_split(part: list[dict], max_dur: float) -> list[Candidate]:
    """Walk word-by-word, close a chunk as soon as adding the next word would
    exceed max_dur. Every emitted chunk lands on a word boundary by construction.
    """
    out: list[Candidate] = []
    chunk: list[dict] = []
    cs = float(part[0]["start"])
    for w in part:
        we = float(w["end"])
        if chunk and (we - cs) > max_dur:
            ce = float(chunk[-1]["end"])
            out.append(Candidate(cs, ce, _join_words(chunk)))
            chunk = []
            cs = float(w["start"])
        chunk.append(w)
    if chunk:
        out.append(Candidate(cs, float(chunk[-1]["end"]), _join_words(chunk)))
    return out


def build_candidates(
    words: list[dict],
    *,
    gap_threshold: float = 0.5,
    max_dur: float = 12.0,
    min_dur: float = 0.4,
) -> list[Candidate]:
    """Group words into phrase-level candidates. Non-overlapping by construction."""
    # Step 1: raw groups by sentence-end punct / silence / speaker change
    raw_groups: list[list[dict]] = []
    current: list[dict] = []
    prev_end: float | None = None
    prev_speaker: str | None = None
    for w in words:
        if w.get("type") != "word":
            continue
        text = (w.get("text") or "").strip()
        if not text:
            continue
        ws = float(w["start"])
        we = float(w["end"])
        speaker = w.get("speaker_id")
        if prev_speaker is not None and speaker is not None and speaker != prev_speaker:
            if current:
                raw_groups.append(current); current = []
        if prev_end is not None and (ws - prev_end) >= gap_threshold:
            if current:
                raw_groups.append(current); current = []
        current.append(w)
        prev_end = we
        prev_speaker = speaker
        if text[-1] in SENT_END_PUNCT:
            raw_groups.append(current); current = []
    if current:
        raw_groups.append(current)

    # Step 2: split groups that exceed max_dur — phrase punct first, then hard
    out: list[Candidate] = []
    for group in raw_groups:
        if not group:
            continue
        start = float(group[0]["start"])
        end = float(group[-1]["end"])
        if end - start <= max_dur:
            out.append(Candidate(start, end, _join_words(group)))
            continue
        parts: list[list[dict]] = []
        buf: list[dict] = []
        for w in group:
            buf.append(w)
            text = (w.get("text") or "").strip()
            if text and text[-1] in PHRASE_PUNCT:
                parts.append(buf); buf = []
        if buf:
            parts.append(buf)
        for part in parts:
            ps = float(part[0]["start"]); pe = float(part[-1]["end"])
            if pe - ps <= max_dur:
                out.append(Candidate(ps, pe, _join_words(part)))
            else:
                out.extend(_hard_split(part, max_dur))

    return [c for c in out if c.duration >= min_dur]


# ============================================================================
# Scoring
# ============================================================================


# Keep word characters, whitespace, and CJK ranges; replace everything else
# (punctuation, brackets, audio-event markers) with a space.
_NORMALIZE_RE = re.compile(
    r"[^\w\s一-鿿㐀-䶿぀-ゟ゠-ヿ가-힯]+",
    flags=re.UNICODE,
)
_WS_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    s = text.casefold()
    s = _NORMALIZE_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    return s


def is_cjk_heavy(text: str) -> bool:
    """True if at least half of the non-whitespace characters are CJK."""
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return False
    cjk = sum(1 for c in chars if CJK_RE.match(c))
    return cjk * 2 >= len(chars)


def _tokens(text: str) -> list[str]:
    return text.split()


def _char_bigrams(text: str) -> set[str]:
    chars = [c for c in text if not c.isspace()]
    return {"".join(chars[i:i + 2]) for i in range(len(chars) - 1)}


def _jaccard(a: set | list, b: set | list) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def text_similarity(cue_text: str, cand_text: str) -> float:
    """Blend of SequenceMatcher (local structure) and Jaccard (bag of units)."""
    a = normalize_text(cue_text)
    b = normalize_text(cand_text)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b, autojunk=False).ratio()
    if is_cjk_heavy(a) or is_cjk_heavy(b):
        jc = _jaccard(_char_bigrams(a), _char_bigrams(b))
    else:
        jc = _jaccard(_tokens(a), _tokens(b))
    return 0.6 * seq + 0.4 * jc


def duration_similarity(cand_dur: float, cue_dur: float) -> float:
    if cue_dur <= 0:
        return 0.0
    delta = abs(cand_dur - cue_dur)
    return 1.0 / (1.0 + delta / cue_dur)


def combined_score(cue: SrtCue, cand: Candidate,
                   w_text: float = 0.7, w_dur: float = 0.3) -> float:
    return (
        w_text * text_similarity(cue.text, cand.text)
        + w_dur * duration_similarity(cand.duration, cue.duration)
    )


# ============================================================================
# Assignment
# ============================================================================


@dataclass
class Assignment:
    cue_id: int
    cue_text: str
    cue_duration: float
    cand: Candidate | None
    score: float
    warnings: list[str] = field(default_factory=list)


def assign(
    cues: list[SrtCue],
    candidates: list[Candidate],
    *,
    allow_reuse: bool = False,
    min_score: float = 0.35,
    duration_warn_ratio: float = 0.5,
    monotonic_source: bool = False,
    max_source_gap_warn: float | None = None,
) -> list[Assignment]:
    """Pick the best candidate for each cue in id order.

    monotonic_source: when True, a candidate is only considered if its
      start time is >= the previously assigned candidate's end. Prevents
      narrative time reversal when the same line appears multiple times
      in the source (the matcher can otherwise pick an earlier instance
      for a later cue).

    max_source_gap_warn: if set, any adjacent assignment pair whose
      absolute source-time gap exceeds the threshold gets a warning.
      Soft signal — does not affect selection.

    Even in non-monotonic mode, a backward source-time jump always
    earns a warning so the review markdown surfaces it.
    """
    used: set[int] = set()
    out: list[Assignment] = []
    # Floor that the NEXT candidate's start must clear under monotonic mode.
    min_start_floor = 0.0

    for cue in cues:
        best_idx = -1
        best_score = -1.0
        for i, cand in enumerate(candidates):
            if not allow_reuse and i in used:
                continue
            if monotonic_source and cand.start < min_start_floor - 1e-6:
                continue
            s = combined_score(cue, cand)
            if s > best_score:
                best_score = s
                best_idx = i

        warns: list[str] = []
        cand_out: Candidate | None = None
        if best_idx < 0:
            if monotonic_source:
                warns.append(
                    f"no candidate available at or after source time "
                    f"{format_srt_ts(min_start_floor)} (monotonic constraint)"
                )
            else:
                warns.append("no candidate available")
            score_out = 0.0
        else:
            cand_out = candidates[best_idx]
            score_out = best_score
            if not allow_reuse:
                used.add(best_idx)
            if monotonic_source:
                # Next cue must start at or after this candidate's end.
                min_start_floor = cand_out.end
            if best_score < min_score:
                warns.append(f"low score {best_score:.3f} < {min_score}")
            if cue.duration > 0:
                dd_ratio = abs(cand_out.duration - cue.duration) / cue.duration
                if dd_ratio > duration_warn_ratio:
                    warns.append(
                        f"duration mismatch: cand {cand_out.duration:.2f}s vs "
                        f"cue {cue.duration:.2f}s ({dd_ratio:.0%} off)"
                    )
            if cand_out.duration + 1e-6 < cue.duration:
                warns.append(
                    "candidate shorter than cue — will need `--on-short pad` "
                    "in srt_driven_edit"
                )
        out.append(Assignment(
            cue_id=cue.id, cue_text=cue.text, cue_duration=cue.duration,
            cand=cand_out, score=score_out, warnings=warns,
        ))

    # Post-pass: surface source-time discontinuities as warnings on the
    # later cue of the pair. Backward jumps are flagged in non-monotonic
    # mode (impossible by construction in monotonic mode). Large gaps are
    # flagged in both modes when --max-source-gap is set.
    for i in range(1, len(out)):
        prev_cand = out[i - 1].cand
        curr_cand = out[i].cand
        if prev_cand is None or curr_cand is None:
            continue
        gap = curr_cand.start - prev_cand.end
        if not monotonic_source and gap < -1e-3:
            out[i].warnings.append(
                f"source time goes backward {gap:+.2f}s: prev cue ends at "
                f"{format_srt_ts(prev_cand.end)}, this cue starts at "
                f"{format_srt_ts(curr_cand.start)}"
            )
        if max_source_gap_warn is not None and abs(gap) > max_source_gap_warn:
            out[i].warnings.append(
                f"source-time jump {gap:+.2f}s exceeds "
                f"--max-source-gap {max_source_gap_warn:.2f}s"
            )

    return out


# ============================================================================
# Output writers
# ============================================================================


def _require_all_assigned(assignments: list[Assignment]) -> None:
    missing = [a.cue_id for a in assignments if a.cand is None]
    if missing:
        raise SystemExit(
            f"no candidate found for cue(s) {missing}. "
            "Add transcript coverage, lower --gap-threshold, or pass --allow-reuse."
        )


def write_plan_form_a(assignments: list[Assignment], out_path: Path) -> None:
    _require_all_assigned(assignments)
    rows = [
        {
            "id": a.cue_id,
            "source_start": format_srt_ts(a.cand.start),
            "source_end": format_srt_ts(a.cand.end),
        }
        for a in assignments
    ]
    out_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def write_plan_form_b(
    assignments: list[Assignment],
    source_path: Path,
    source_name: str,
    out_path: Path,
) -> None:
    _require_all_assigned(assignments)
    data = {
        "sources": {source_name: str(source_path)},
        "segments": [
            {
                "id": a.cue_id,
                "source": source_name,
                "source_start": format_srt_ts(a.cand.start),
                "source_end": format_srt_ts(a.cand.end),
            }
            for a in assignments
        ],
    }
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_review(assignments: list[Assignment], out_path: Path) -> None:
    lines: list[str] = ["# Edit plan review", ""]
    total = len(assignments)
    matched = sum(1 for a in assignments if a.cand is not None)
    warned = sum(1 for a in assignments if a.warnings)
    avg = (sum(a.score for a in assignments if a.cand) / max(matched, 1))
    lines.append(f"- total cues: {total}")
    lines.append(f"- matched: {matched}/{total}")
    lines.append(f"- with warnings: {warned}")
    lines.append(f"- average score: {avg:.3f}")
    lines.append("")
    for a in assignments:
        lines.append(f"## cue id={a.cue_id}")
        lines.append(f"- **cue text**: {a.cue_text!r}")
        lines.append(f"- **cue duration**: {a.cue_duration:.3f}s")
        if a.cand is None:
            lines.append("- **match**: NONE")
        else:
            lines.append(f"- **matched text**: {a.cand.text!r}")
            lines.append(
                f"- **source range**: {format_srt_ts(a.cand.start)} → "
                f"{format_srt_ts(a.cand.end)} ({a.cand.duration:.3f}s)"
            )
            lines.append(f"- **score**: {a.score:.3f}")
            dd = a.cand.duration - a.cue_duration
            lines.append(f"- **duration delta**: {dd:+.3f}s")
        for w in a.warnings:
            lines.append(f"- **WARNING**: {w}")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


# ============================================================================
# Top-level callable (used by CLI and tests)
# ============================================================================


def recommend(
    *,
    script_srt: Path,
    transcript: Path,
    source: Path,
    output: Path,
    review: Path | None = None,
    source_name: str = "A",
    output_format: str = "form-a",
    gap_threshold: float = 0.5,
    max_cand_dur: float = 12.0,
    min_cand_dur: float = 0.4,
    min_score: float = 0.35,
    allow_reuse: bool = False,
    keep_audio_events: bool = False,
    monotonic_source: bool = False,
    max_source_gap_warn: float | None = None,
) -> list[Assignment]:
    cues = _parse_srt(script_srt)
    if not cues:
        raise SystemExit(f"script.srt has no cues: {script_srt}")

    words = load_transcript_words(transcript, keep_audio_events=keep_audio_events)
    candidates = build_candidates(
        words,
        gap_threshold=gap_threshold,
        max_dur=max_cand_dur,
        min_dur=min_cand_dur,
    )
    if not candidates:
        raise SystemExit(
            f"no candidates built from transcript {transcript}. "
            "Try lowering --min-cand-dur or check transcript quality."
        )

    assignments = assign(
        cues, candidates,
        allow_reuse=allow_reuse, min_score=min_score,
        monotonic_source=monotonic_source,
        max_source_gap_warn=max_source_gap_warn,
    )

    if output_format == "form-a":
        write_plan_form_a(assignments, output)
    elif output_format == "form-b":
        write_plan_form_b(assignments, source, source_name, output)
    else:
        raise SystemExit(f"unknown --format: {output_format}")

    if review is None:
        review = output.with_name(output.stem + "_review.md")
    write_review(assignments, review)
    return assignments


# ============================================================================
# CLI
# ============================================================================


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Recommend edit_plan.json from script.srt + Scribe transcript",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python helpers/recommend_edit_plan.py \\\n"
            "    --script script.srt \\\n"
            "    --transcript edit/transcripts/source.json \\\n"
            "    --source source.mp4 \\\n"
            "    -o edit_plan.json\n"
            "  python helpers/srt_driven_edit.py \\\n"
            "    --source source.mp4 --srt script.srt --plan edit_plan.json -o final.mp4"
        ),
    )
    ap.add_argument("--script", type=Path, required=True,
                    help="script.srt (target captions timeline)")
    ap.add_argument("--transcript", type=Path, required=True,
                    help="Scribe transcript JSON")
    ap.add_argument("--source", type=Path, required=True,
                    help="source.mp4 path (recorded in Form-B plans)")
    ap.add_argument("--packed", type=Path, default=None,
                    help="optional takes_packed.md (reserved; unused in v1)")
    ap.add_argument("--source-name", default="A",
                    help="Form-B source name (default 'A')")
    ap.add_argument("--context-window", type=float, default=1.5,
                    help="reserved for future use")
    ap.add_argument("--gap-threshold", type=float, default=0.5,
                    help="silence gap (s) that breaks a candidate. default 0.5")
    ap.add_argument("--max-cand-dur", type=float, default=12.0,
                    help="max candidate duration before forced split. default 12.0")
    ap.add_argument("--min-cand-dur", type=float, default=0.4,
                    help="drop candidates shorter than this. default 0.4")
    ap.add_argument("--min-score", type=float, default=0.35,
                    help="score below this triggers a warning. default 0.35")
    ap.add_argument("--allow-reuse", action="store_true",
                    help="allow one candidate to be assigned to multiple cues")
    ap.add_argument("--keep-audio-events", action="store_true",
                    help="keep (laughter) (applause) tokens as candidate context")
    ap.add_argument("--monotonic-source", action="store_true",
                    help="require each cue's source range to start at or after "
                         "the previous cue's match. Prevents narrative time "
                         "reversal when the same line appears multiple times "
                         "in the source.")
    ap.add_argument("--max-source-gap", type=float, default=None,
                    help="seconds. When set, any adjacent assignment whose "
                         "|source-time gap| exceeds this earns a warning.")
    ap.add_argument("--format", choices=["form-a", "form-b"], default="form-a",
                    dest="output_format")
    ap.add_argument("-o", "--output", type=Path, required=True,
                    help="edit_plan.json path")
    ap.add_argument("--review", type=Path, default=None,
                    help="review .md path (default: <output>_review.md)")
    args = ap.parse_args()

    assignments = recommend(
        script_srt=args.script.resolve(),
        transcript=args.transcript.resolve(),
        source=args.source.resolve(),
        output=args.output.resolve(),
        review=args.review.resolve() if args.review else None,
        source_name=args.source_name,
        output_format=args.output_format,
        gap_threshold=args.gap_threshold,
        max_cand_dur=args.max_cand_dur,
        min_cand_dur=args.min_cand_dur,
        min_score=args.min_score,
        allow_reuse=args.allow_reuse,
        keep_audio_events=args.keep_audio_events,
        monotonic_source=args.monotonic_source,
        max_source_gap_warn=args.max_source_gap,
    )

    matched = sum(1 for a in assignments if a.cand is not None)
    warned = sum(1 for a in assignments if a.warnings)
    avg = sum(a.score for a in assignments if a.cand is not None) / max(matched, 1)
    review_path = (
        args.review.resolve() if args.review
        else args.output.resolve().with_name(args.output.stem + "_review.md")
    )
    print(f"wrote plan → {args.output}")
    print(f"wrote review → {review_path}")
    print(f"  {matched}/{len(assignments)} cues matched, avg score {avg:.3f}, "
          f"{warned} with warnings")


if __name__ == "__main__":
    main()
