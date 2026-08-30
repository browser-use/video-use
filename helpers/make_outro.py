#!/usr/bin/env python3
"""
make_outro.py — one-command editorial outro for @derblasseschimmer Reels.

Usage:
    uv run python helpers/make_outro.py \
        --source  "Reel.mp4"           \
        --cutpoint 39.16               \
        --content  outro_content.json  \
        --out      "edit/Reel - neues Outro.mp4"

Content JSON schema:
    {
      "stage1": {
        "kicker":  "ZUM MERKEN",
        "context": "Merk Dir das für Deine",
        "hero":    "Wunschliste"
      },
      "stage2": {
        "kicker":  "BALD BEI MIR",
        "context": "Drei neue Skin Tint Balms",
        "hero":    "im direkten / Vergleich",   ← '/' becomes <br>
        "handle":  "@derblasseschimmer"
      },
      "veil_opacity": 0.88,    ← optional, default 0.88
      "outro_duration": 7.0    ← optional, default 7.0
    }

Hard rules enforced automatically:
    - Luminance measured from cutpoint frame → text/veil colors chosen
    - No zoompan (causes stuttering — static still only)
    - 30ms audio fades at cut boundary
    - HyperFrames PNG-sequence for frame-perfect overlay
    - 3 verification frames extracted after render
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


SKILL_DIR = Path(__file__).parent.parent
TEMPLATE   = SKILL_DIR / "templates" / "outro" / "outro_template.html"
HYPERFRAMES_INIT = ["npx", "--yes", "hyperframes", "init", ".",
                    "--example", "blank", "--non-interactive", "--skip-skills"]


# ── helpers ──────────────────────────────────────────────────────────────────

def run(cmd, **kw):
    print(f"  $ {' '.join(str(c) for c in cmd)}")
    result = subprocess.run(cmd, check=True, **kw)
    return result


def measure_luminance(frame_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-f", "lavfi", f"-i", f"movie={frame_path},signalstats",
        "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
        "-of", "json"
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    frames = json.loads(out).get("frames", [])
    if not frames:
        raise RuntimeError("signalstats returned no frames")
    return round(float(frames[0]["tags"]["lavfi.signalstats.YAVG"]) / 255 * 100, 1)


def luminance_strategy(L: float) -> dict:
    if L > 65:
        return dict(
            text_color="rgba(30,30,30,0.92)",
            text_shadow="0px 1px 8px rgba(255,255,255,0.6), 0px 0px 20px rgba(255,255,255,0.3)",
            veil_color="#FAF6F0",
            label="HELL"
        )
    elif L < 40:
        return dict(
            text_color="#FAF6F0",
            text_shadow="0 2px 30px rgba(44,37,35,0.35), 0 1px 4px rgba(44,37,35,0.25)",
            veil_color="#2C2523",
            label="DUNKEL"
        )
    else:
        return dict(
            text_color="#FFFFFF",
            text_shadow="0 2px 14px rgba(44,37,35,0.45), 0 1px 4px rgba(44,37,35,0.25)",
            veil_color="#2C2523",
            label="MITTEL"
        )


def inject_config(template_path: Path, slot_dir: Path, config: dict):
    """Write outro_template.html into slot/index.html with config injected."""
    src = template_path.read_text()
    inject_line = f"window.__OUTRO_CONFIG__ = {json.dumps(config, ensure_ascii=False)};"
    out = src.replace("// __OUTRO_CONFIG_INJECT__", inject_line)
    # Fix data-duration to match outro_duration
    dur = config.get("outro_duration", 7.0)
    out = re.sub(r'data-duration="\d+[\.\d]*"', f'data-duration="{dur}"', out)
    (slot_dir / "index.html").write_text(out)


def ffmpeg(*args):
    run(["ffmpeg", "-y", *[str(a) for a in args]])


def has_audio(path: str) -> bool:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_streams", "-select_streams", "a", str(path)],
        capture_output=True, text=True, check=True
    ).stdout
    return bool(json.loads(out).get("streams"))


def get_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", path],
        capture_output=True, text=True, check=True
    ).stdout
    return float(json.loads(out)["format"]["duration"])


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source",    required=True, help="Source video")
    parser.add_argument("--cutpoint",  type=float, required=True,
                        help="Timestamp (s) where outro begins")
    parser.add_argument("--content",   required=True,
                        help="JSON file with stage1/stage2 copy")
    parser.add_argument("--out",       required=True, help="Output MP4 path")
    parser.add_argument("--slot-dir",  default=None,
                        help="Animation slot dir (default: <out_dir>/animations/slot_outro)")
    args = parser.parse_args()

    source    = Path(args.source).resolve()
    out_path  = Path(args.out).resolve()
    out_dir   = out_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(args.content) as f:
        content = json.load(f)

    outro_duration = content.get("outro_duration", 7.0)
    veil_opacity   = content.get("veil_opacity",   0.88)

    slot_dir = Path(args.slot_dir) if args.slot_dir else \
               out_dir / "animations" / "slot_outro"
    slot_dir.mkdir(parents=True, exist_ok=True)

    # ── Step 1: extract frame at cutpoint ────────────────────────────────────
    print(f"\n[1/7] Frame bei {args.cutpoint}s extrahieren …")
    bg_frame = slot_dir / "bg_still.png"
    ffmpeg("-ss", args.cutpoint, "-i", source,
           "-frames:v", "1", "-update", "1", str(bg_frame))

    # ── Step 2: measure luminance ─────────────────────────────────────────────
    print("\n[2/7] Luminanz messen …")
    L = measure_luminance(str(bg_frame))
    strat = luminance_strategy(L)
    print(f"  Luminanz: {L:.1f}/100 → {strat['label']} "
          f"(Veil: {strat['veil_color']}, Text: {strat['text_color']})")

    # ── Step 3: inject config into template ───────────────────────────────────
    print("\n[3/7] Template mit Config befüllen …")
    config = {
        **content,
        "background_luminance": L,
        "veil_opacity": veil_opacity,
        "outro_duration": outro_duration,
    }

    # Init HyperFrames slot if not already done
    hf_marker = slot_dir / "hyperframes.json"
    if not hf_marker.exists():
        run(HYPERFRAMES_INIT, cwd=slot_dir)

    inject_config(TEMPLATE, slot_dir, config)

    # ── Step 4: render HyperFrames PNG sequence ───────────────────────────────
    print("\n[4/7] HyperFrames PNG-Sequence rendern …")
    frames_dir = slot_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    run(["npx", "--yes", "hyperframes", "render", ".",
         "--format", "png-sequence", "-o", "frames"],
        cwd=slot_dir)

    # ── Step 5: composite still + overlay ────────────────────────────────────
    print("\n[5/7] Still + Overlay compositen …")
    outro_clip = slot_dir / "outro_composite.mp4"
    fps = 30
    n_frames = fps * outro_duration
    ffmpeg(
        "-loop", "1", "-framerate", fps, "-t", outro_duration, "-i", bg_frame,
        "-framerate", fps, "-i", str(slot_dir / "frames" / "frame_%06d.png"),
        "-filter_complex",
        "[0:v]scale=1080:1920,format=yuva420p[bg];"
        "[1:v]format=yuva420p[ov];"
        "[bg][ov]overlay=format=auto,format=yuv420p[out]",
        "-map", "[out]",
        "-t", outro_duration, "-r", "25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "slow",
        outro_clip
    )

    # ── Step 6: assemble final video ──────────────────────────────────────────
    print("\n[6/7] Final Video zusammenbauen …")
    total_duration = args.cutpoint + outro_duration
    audio_fade_st  = max(0.0, args.cutpoint - 0.03)

    source_has_audio = has_audio(str(source))

    if source_has_audio:
        # Trim audio at cutpoint, 30ms fade at cut boundary, pad silence for outro
        audio_filter = (
            f"[0:a]atrim=0:{args.cutpoint},asetpts=PTS-STARTPTS,"
            f"afade=t=out:st={audio_fade_st:.3f}:d=0.03,"
            f"apad=whole_dur={total_duration}[aout]"
        )
        a_map   = ["-map", "[aout]"]
        a_codec = ["-c:a", "aac", "-b:a", "192k"]
    else:
        audio_filter = f"aevalsrc=0:d={total_duration}[aout]"
        a_map   = ["-map", "[aout]"]
        a_codec = ["-c:a", "aac", "-b:a", "192k"]

    ffmpeg(
        "-i", source, "-i", outro_clip,
        "-filter_complex",
        f"[0:v]trim=0:{args.cutpoint},setpts=PTS-STARTPTS[mv];"
        f"[mv][1:v]concat=n=2:v=1:a=0[v];"
        + audio_filter,
        "-map", "[v]", *a_map,
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "16", "-preset", "slow", "-r", "25",
        *a_codec,
        "-t", total_duration,
        out_path
    )

    # ── Step 7: verify ────────────────────────────────────────────────────────
    print("\n[7/7] Verifikation …")
    verify_dir = out_dir / "verify"
    verify_dir.mkdir(exist_ok=True)

    checkpoints = [
        args.cutpoint + 0.3,                   # outro-start
        args.cutpoint + outro_duration * 0.45, # stage 1 peak
        args.cutpoint + outro_duration * 0.90, # stage 2 peak
    ]
    for t in checkpoints:
        out_frame = verify_dir / f"outro_{t:.1f}s.jpg"
        ffmpeg("-ss", f"{t:.3f}", "-i", out_path,
               "-frames:v", "1", "-update", "1", "-q:v", "2", out_frame)
        print(f"  ✓ verify frame @ {t:.1f}s → {out_frame.name}")

    final_dur = get_duration(str(out_path))
    print(f"\n✅ Fertig: {out_path}")
    print(f"   Dauer: {final_dur:.2f}s | Luminanz: {L:.1f} ({strat['label']})")
    print(f"   Verifikations-Frames: {verify_dir}/\n")


if __name__ == "__main__":
    main()
