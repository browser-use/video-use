#!/usr/bin/env python3
"""
check_luminance.py — measure average luminance of a video frame or image.

Usage:
    uv run python helpers/check_luminance.py <file>
    uv run python helpers/check_luminance.py <video> --time 39.16

Returns:
    Luminance value (0–100) and recommended text/veil strategy.
"""
import argparse
import subprocess
import sys
import json
import tempfile
import os


def measure_luminance(image_path: str) -> float:
    """Measure average Y (luma) of an image via ffprobe signalstats."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-f", "lavfi",
        "-i", f"movie={image_path},signalstats",
        "-show_entries", "frame_tags=lavfi.signalstats.YAVG",
        "-of", "json"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    data = json.loads(result.stdout)
    frames = data.get("frames", [])
    if not frames:
        raise RuntimeError(f"Could not read luminance from {image_path}")
    yavg = float(frames[0]["tags"]["lavfi.signalstats.YAVG"])
    # ffprobe returns Y in 0-255 range for 8-bit; normalize to 0-100
    return round(yavg / 255 * 100, 1)


def extract_frame(video_path: str, time: float, out_path: str):
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(time), "-i", video_path,
        "-frames:v", "1", "-update", "1", "-q:v", "2", out_path
    ], capture_output=True, check=True)


def strategy(L: float) -> dict:
    if L > 65:
        return {
            "text_color": "rgba(30,30,30,0.92)",
            "text_shadow": "0px 1px 8px rgba(255,255,255,0.6), 0px 0px 20px rgba(255,255,255,0.3)",
            "veil_color": "#FAF6F0",
            "label": "HELL → dunkle Schrift + cremefarbener Veil",
        }
    elif L < 40:
        return {
            "text_color": "#FAF6F0",
            "text_shadow": "0 2px 30px rgba(44,37,35,0.35), 0 1px 4px rgba(44,37,35,0.25)",
            "veil_color": "#2C2523",
            "label": "DUNKEL → helle Schrift + Espresso-Veil",
        }
    else:
        return {
            "text_color": "#FFFFFF",
            "text_shadow": "0 2px 14px rgba(44,37,35,0.45), 0 1px 4px rgba(44,37,35,0.25)",
            "veil_color": "#2C2523",
            "label": "MITTEL → weiße Schrift + dunkler Veil",
        }


def main():
    parser = argparse.ArgumentParser(description="Measure frame luminance and recommend text strategy.")
    parser.add_argument("file", help="Image (.jpg/.png) or video file")
    parser.add_argument("--time", type=float, default=None,
                        help="Timestamp (seconds) to sample from video")
    parser.add_argument("--json", action="store_true", help="Output JSON only")
    args = parser.parse_args()

    file = args.file
    tmp = None

    if args.time is not None:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
            tmp = tf.name
        extract_frame(file, args.time, tmp)
        file = tmp

    try:
        L = measure_luminance(file)
        s = strategy(L)

        if args.json:
            print(json.dumps({"luminance": L, **s}))
        else:
            print(f"\nLuminanz:     {L:.1f} / 100")
            print(f"Strategie:    {s['label']}")
            print(f"Textfarbe:    {s['text_color']}")
            print(f"Veil-Farbe:   {s['veil_color']}")
            print(f"Text-Shadow:  {s['text_shadow']}\n")
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


if __name__ == "__main__":
    main()
