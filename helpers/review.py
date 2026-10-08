"""Review a rendered cut in the browser and read the notes back.

Two jobs, no server:

    python helpers/review.py <video>            generate the page and open it
    python helpers/review.py --dump <notes>     print the notes for the agent

The page is a plain file opened from disk. Measured on Chrome 153, a file://
page is a secure context, gets MediaRecorder and showDirectoryPicker, and seeks
150 s into a 380 MB local file in 201 ms. That last number is why there is no
server here: HTTP Range support was the only thing a server would have added.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import webbrowser
from fractions import Fraction
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render import parse_fps, probe_source_fps  # noqa: E402  same directory
from transcribe import call_scribe, extract_audio, load_api_key  # noqa: E402

KINDS = ("cut", "shorten", "lengthen", "wrong", "note")

TEMPLATE = Path(__file__).resolve().parent / "review.html"
PARAMS_MARKER = '<script id="review-params" type="application/json">'


def fps_to_float(canonical: str) -> float:
    """A frame rate as a number. Accepts what parse_fps accepts.

    parse_fps signals a bad rate with argparse.ArgumentTypeError, which is not a
    ValueError. Callers here are not a command line, so the error is translated
    rather than leaked.
    """
    try:
        rate = Fraction(parse_fps(canonical))
    except (argparse.ArgumentTypeError, ZeroDivisionError, TypeError) as exc:
        raise ValueError(f"not a usable frame rate: {canonical!r}") from exc
    return float(rate)


def time_to_frame(seconds: float, fps: float) -> int:
    """Which frame an instant falls on. A note that says 'cut' has to become a
    cut later, and that needs a frame, not a float."""
    return max(0, round(seconds * fps))


def format_timecode(seconds: float, fps: float) -> str:
    """m:ss.ff - the only thing the reviewer reads to orient themselves.

    Minutes are not wrapped into hours: a cut is discussed as '21:04', and a
    reviewer who sees '1:01:04' has to do arithmetic to find it in the EDL.
    """
    seconds = max(0.0, seconds)
    minutes = int(seconds // 60)
    rest = seconds - minutes * 60
    whole = int(rest)
    frames = int(round((rest - whole) * fps))
    if frames >= round(fps):
        frames = 0
        whole += 1
        if whole >= 60:
            whole = 0
            minutes += 1
    return f"{minutes}:{whole:02d}.{frames:02d}"


def relative_video_path(video: Path, page_dir: Path) -> str:
    """How the page refers to the video, URL-encoded.

    Refuses instead of guessing when the two cannot be expressed relative to
    each other, which in practice means separate Windows drives. A <video> that
    silently fails to load is worse than a command that refuses to run, so the
    result is also walked back to the file it came from before it is returned.
    """
    video = video.resolve()
    page_dir = page_dir.resolve()
    try:
        rel = os.path.relpath(video, page_dir)
    except ValueError as exc:
        raise ValueError(
            f"cannot reach {video} from {page_dir}: put the page on the same volume"
        ) from exc
    if os.path.isabs(rel) or os.path.normpath(os.path.join(page_dir, rel)) != str(video):
        raise ValueError(f"cannot reach {video} from {page_dir}")
    return quote(Path(rel).as_posix())


def probe_duration(video: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(video)],
        capture_output=True, text=True, check=True,
    )
    return float(out.stdout.strip())


def read_params(page: Path) -> dict:
    """The parameter block back out of a generated page. Used by the tests and
    by anyone debugging a page that will not load."""
    html = page.read_text(encoding="utf-8")
    start = html.index(PARAMS_MARKER) + len(PARAMS_MARKER)
    return json.loads(html[start:html.index("</script>", start)])


def build_page(video: Path, out_dir: Path) -> Path:
    """Write the review page for a video. Returns its path."""
    if not TEMPLATE.exists():
        raise FileNotFoundError(f"template missing: {TEMPLATE}")
    out_dir.mkdir(parents=True, exist_ok=True)

    params = {
        "video": relative_video_path(video, out_dir),
        "stem": video.stem,
        "fps": probe_source_fps(video) or "25",
        "duration": probe_duration(video),
    }
    # "<" becomes an escape so no path can close the script tag and turn the
    # parameter block into markup.
    blob = json.dumps(params, ensure_ascii=False).replace("<", "\\u003c")

    html = TEMPLATE.read_text(encoding="utf-8")
    start = html.index(PARAMS_MARKER) + len(PARAMS_MARKER)
    end = html.index("</script>", start)
    page = out_dir / f"{video.stem}.html"
    page.write_text(html[:start] + blob + html[end:], encoding="utf-8")
    return page


def transcribe_pending(data: dict, notes_path: Path) -> int:
    """Fill in voice_text for voice notes that lack it. Returns how many were done.

    The page never calls Scribe: that would mean shipping the API key inside an
    HTML file. It records and saves; the transcription happens here, where the
    key already lives.

    The result is written back into the notes file so a second --dump does not
    pay for the same audio twice.
    """
    pending = [n for n in data["notes"] if n.get("voice") and not n.get("voice_text")]
    if not pending:
        return 0
    try:
        api_key = load_api_key()
    except SystemExit:
        print("no ELEVENLABS_API_KEY: leaving voice notes untranscribed", file=sys.stderr)
        return 0

    done = 0
    for note in pending:
        audio = (notes_path.parent / note["voice"]).resolve()
        if not audio.exists():
            print(f"voice file missing, skipped: {note['voice']}", file=sys.stderr)
            continue
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "voice.wav"
            # The same extraction the rest of the skill uses: Scribe gets 16 kHz
            # mono wav whatever the browser recorded.
            extract_audio(audio, wav)
            payload = call_scribe(wav, api_key)
        note["voice_text"] = (payload.get("text") or "").strip()
        done += 1

    if done:
        notes_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return done


def load_notes(path: Path) -> dict:
    """Read a notes file, complaining usefully when it is not one.

    A crash here loses somebody's review, so the failure says which file and
    why rather than surfacing a bare JSONDecodeError.
    """
    if not path.exists():
        raise FileNotFoundError(f"notes file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{path.name} is not valid JSON ({exc.msg}, line {exc.lineno})"
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("notes"), list):
        raise ValueError(
            f"{path.name} does not look like a review file (no 'notes' list)"
        )
    return data


def dump_notes(path: Path, transcribe: bool = True) -> str:
    """The notes as markdown, in time order, for the agent to read."""
    data = load_notes(path)
    fps = fps_to_float(data.get("fps") or "25")

    if transcribe:
        transcribe_pending(data, path)
    notes = sorted(data["notes"], key=lambda n: (n.get("t_in") or 0.0))

    duration = data.get("duration") or 0.0
    lines = [
        f"# Review of {data.get('video', '?')}",
        "",
        f"{len(notes)} notes on {duration:.1f}s at {data.get('fps')} fps",
        "",
    ]
    for n in notes:
        when = format_timecode(n.get("t_in") or 0.0, fps)
        frames = f"frame {n.get('frame_in')}"
        if n.get("t_out") is not None:
            when += f" - {format_timecode(n['t_out'], fps)}"
            frames = f"frames {n.get('frame_in')}-{n.get('frame_out')}"
        # A note can also point at a spot inside the frame. The percentages are
        # of the picture, not of the window, so they survive any player size.
        if n.get("x") is not None and n.get("y") is not None:
            frames += " at {:.0%},{:.0%} of the frame".format(n["x"], n["y"])
        body = (n.get("text") or "").strip()
        spoken = (n.get("voice_text") or "").strip()
        if spoken:
            body = f"{body} (spoken: {spoken})" if body else f"(spoken) {spoken}"
        if not body and n.get("voice"):
            body = f"(voice note not transcribed: {n['voice']})"
        lines.append(
            f"- **{when}** `{n.get('kind', 'note')}` ({frames}): {body or '(no text)'}"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate a browser review page for a cut, or read its notes back")
    ap.add_argument("target", type=Path,
                    help="video to review, or the notes JSON when using --dump")
    ap.add_argument("--dump", action="store_true",
                    help="print the notes as markdown instead of generating a page")
    ap.add_argument("--no-transcribe", action="store_true",
                    help="with --dump, skip Scribe and leave voice notes as file paths")
    ap.add_argument("--out", type=Path, default=None,
                    help="where to write the page (default: <video_parent>/review)")
    ap.add_argument("--no-open", action="store_true", help="do not open the browser")
    args = ap.parse_args()

    target = args.target.expanduser()
    if not target.exists():
        sys.exit(f"not found: {target}")

    if args.dump:
        print(dump_notes(target.resolve(), transcribe=not args.no_transcribe), end="")
        return

    video = target.resolve()
    out_dir = (args.out or video.parent / "review").resolve()
    page = build_page(video, out_dir)
    notes = out_dir / f"{video.stem}.review.json"
    print(f"page:  {page}")
    print(f"notes: {notes}  ({'exists' if notes.exists() else 'not written yet'})")
    print(f"read them back with:  python {Path(__file__).name} --dump {notes}")
    if not args.no_open:
        webbrowser.open(page.as_uri())


if __name__ == "__main__":
    main()
