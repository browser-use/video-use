#!/usr/bin/env python3
"""GitHub Copilot-backed video editing orchestrator for video-use.

Replaces the `claude` CLI runtime with a standalone Python script that drives
the same video editing pipeline using the GitHub Copilot API (OpenAI-compatible).
All 12 hard production rules from SKILL.md are enforced via the same system
prompt — no logic changes to the skill or helpers are needed.

Requirements:
  pip install -e ".[copilot]"          # openai>=1.0
  export GITHUB_TOKEN=<your PAT>       # PAT with `copilot` scope
  ELEVENLABS_API_KEY=... in .env       # for transcription (same as before)
  ffmpeg and ffprobe on PATH

Usage:
  python orchestrator.py /path/to/videos
  python orchestrator.py /path/to/videos --model gpt-4o
  python orchestrator.py /path/to/videos --endpoint https://models.inference.ai.azure.com
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Repo-relative paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
HELPERS_DIR = REPO_ROOT / "helpers"
SKILL_MD = REPO_ROOT / "SKILL.md"

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


def load_skill_prompt() -> str:
    """Read SKILL.md and strip the YAML front matter used by Claude Code."""
    text = SKILL_MD.read_text()
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :].lstrip("\n")
    return text


# Maximum characters returned from a single tool call before truncation.
# Keeps large files (packed transcripts, long ffmpeg logs) from consuming
# the entire context window.
MAX_TOOL_RESULT_LENGTH = 20_000

# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "transcribe",
            "description": (
                "Transcribe a single video with ElevenLabs Scribe. "
                "Writes word-level transcript JSON to edit/transcripts/<stem>.json. "
                "Cached — skips upload if the JSON already exists."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "video_path": {
                        "type": "string",
                        "description": "Absolute path to the video file.",
                    },
                    "edit_dir": {
                        "type": "string",
                        "description": "Edit output directory. Defaults to <video_parent>/edit.",
                    },
                    "language": {
                        "type": "string",
                        "description": "ISO language code (e.g. 'en'). Omit to auto-detect.",
                    },
                    "num_speakers": {
                        "type": "integer",
                        "description": "Number of speakers. Improves diarization when known.",
                    },
                },
                "required": ["video_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "transcribe_batch",
            "description": (
                "Batch-transcribe every video in a directory using parallel workers. "
                "Cached per source — already-transcribed files are skipped."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "videos_dir": {
                        "type": "string",
                        "description": "Directory containing source videos.",
                    },
                    "workers": {
                        "type": "integer",
                        "description": "Parallel workers (default 4).",
                    },
                    "edit_dir": {
                        "type": "string",
                        "description": "Override edit output directory.",
                    },
                    "num_speakers": {
                        "type": "integer",
                        "description": "Number of speakers (optional).",
                    },
                },
                "required": ["videos_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pack_transcripts",
            "description": (
                "Pack all per-source transcript JSONs in edit/transcripts/ into "
                "takes_packed.md — the primary phrase-level reading surface for cut decisions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "edit_dir": {
                        "type": "string",
                        "description": "Edit output directory containing transcripts/ subdirectory.",
                    },
                    "silence_threshold": {
                        "type": "number",
                        "description": "Silence gap in seconds that triggers a phrase break (default 0.5).",
                    },
                },
                "required": ["edit_dir"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "timeline_view",
            "description": (
                "Generate a filmstrip + waveform PNG for a time range of a video. "
                "Use at decision points (ambiguous pauses, retake comparison, cut-point "
                "sanity checks). NOT a scan tool — call only when you need a visual check."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "video_path": {
                        "type": "string",
                        "description": "Absolute path to the video file.",
                    },
                    "start": {
                        "type": "number",
                        "description": "Start time in seconds.",
                    },
                    "end": {
                        "type": "number",
                        "description": "End time in seconds.",
                    },
                    "n_frames": {
                        "type": "integer",
                        "description": "Number of filmstrip frames to extract (default 8).",
                    },
                    "transcript_path": {
                        "type": "string",
                        "description": "Optional path to a transcript JSON for word label overlay.",
                    },
                },
                "required": ["video_path", "start", "end"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "render",
            "description": (
                "Render a video from an EDL (edit decision list JSON). "
                "Runs the full pipeline: per-segment extract with grade + 30ms audio fades → "
                "lossless concat → overlays (PTS-shifted) → subtitles LAST → loudnorm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "edl_path": {
                        "type": "string",
                        "description": "Absolute path to edl.json.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output video path (e.g. edit/final.mp4).",
                    },
                    "preview": {
                        "type": "boolean",
                        "description": "Preview mode: 1080p, CRF 22, faster encode.",
                    },
                    "build_subtitles": {
                        "type": "boolean",
                        "description": "Build master.srt from transcripts + EDL timeline offsets.",
                    },
                    "no_subtitles": {
                        "type": "boolean",
                        "description": "Skip subtitles even if the EDL references one.",
                    },
                    "no_loudnorm": {
                        "type": "boolean",
                        "description": "Skip audio loudness normalization (default: on, -14 LUFS).",
                    },
                },
                "required": ["edl_path", "output_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "grade",
            "description": (
                "Apply a color grade to a video via ffmpeg filter chain. "
                "Presets: subtle, neutral_punch, warm_cinematic, none. "
                "Omit both preset and filter for auto mode (data-driven per-clip correction)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {
                        "type": "string",
                        "description": "Input video path.",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Output video path.",
                    },
                    "preset": {
                        "type": "string",
                        "description": "Grade preset name.",
                        "enum": ["subtle", "neutral_punch", "warm_cinematic", "none"],
                    },
                    "filter": {
                        "type": "string",
                        "description": "Raw ffmpeg filter string (overrides preset).",
                    },
                },
                "required": ["input_path", "output_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command. Use for ffprobe, yt-dlp, file listing, "
                "ffmpeg one-offs, and other system tasks the other tools don't cover."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Shell command to execute.",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a text file (takes_packed.md, project.md, edl.json, transcripts, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write or append content to a file (edl.json, project.md, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to the file.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Content to write.",
                    },
                    "append": {
                        "type": "boolean",
                        "description": "If true, append to existing file instead of overwriting.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------


def _run_helper(args: list[str]) -> tuple[int, str, str]:
    """Run a Python helper from the helpers/ directory."""
    cmd = [sys.executable] + args
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def _format_result(returncode: int, stdout: str, stderr: str) -> str:
    parts: list[str] = []
    if stdout.strip():
        parts.append(stdout.strip())
    if returncode != 0 and stderr.strip():
        parts.append(f"[stderr]\n{stderr.strip()}")
    if not parts:
        parts.append("(no output)" if returncode == 0 else f"[exit {returncode}] (no output)")
    if returncode != 0:
        parts.insert(0, f"[exit code {returncode}]")
    return "\n".join(parts)


def dispatch_tool(
    name: str,
    args: dict,
    videos_dir: Path,
    edit_dir: Path,
) -> tuple[str, Path | None]:
    """Execute a tool call. Returns (result_text, optional_image_path)."""

    if name == "transcribe":
        video_path = args["video_path"]
        cmd = [str(HELPERS_DIR / "transcribe.py"), video_path]
        if args.get("edit_dir"):
            cmd += ["--edit-dir", args["edit_dir"]]
        else:
            cmd += ["--edit-dir", str(edit_dir)]
        if args.get("language"):
            cmd += ["--language", args["language"]]
        if args.get("num_speakers"):
            cmd += ["--num-speakers", str(args["num_speakers"])]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err), None

    if name == "transcribe_batch":
        cmd = [str(HELPERS_DIR / "transcribe_batch.py"), args["videos_dir"]]
        if args.get("edit_dir"):
            cmd += ["--edit-dir", args["edit_dir"]]
        if args.get("workers"):
            cmd += ["--workers", str(args["workers"])]
        if args.get("num_speakers"):
            cmd += ["--num-speakers", str(args["num_speakers"])]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err), None

    if name == "pack_transcripts":
        cmd = [str(HELPERS_DIR / "pack_transcripts.py"), "--edit-dir", args["edit_dir"]]
        if args.get("silence_threshold") is not None:
            cmd += ["--silence-threshold", str(args["silence_threshold"])]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err), None

    if name == "timeline_view":
        video_path = Path(args["video_path"])
        start = args["start"]
        end = args["end"]
        verify_dir = edit_dir / "verify"
        verify_dir.mkdir(parents=True, exist_ok=True)
        out_img = verify_dir / f"timeline_{video_path.stem}_{start:.2f}_{end:.2f}.png"
        cmd = [
            str(HELPERS_DIR / "timeline_view.py"),
            str(video_path),
            str(start),
            str(end),
            "-o", str(out_img),
        ]
        if args.get("n_frames"):
            cmd += ["--n-frames", str(args["n_frames"])]
        if args.get("transcript_path"):
            cmd += ["--transcript", args["transcript_path"]]
        rc, out, err = _run_helper(cmd)
        result = _format_result(rc, out, err)
        if rc == 0 and out_img.exists():
            result += f"\nImage saved to: {out_img}"
            return result, out_img
        return result, None

    if name == "render":
        cmd = [
            str(HELPERS_DIR / "render.py"),
            args["edl_path"],
            "-o", args["output_path"],
        ]
        if args.get("preview"):
            cmd.append("--preview")
        if args.get("build_subtitles"):
            cmd.append("--build-subtitles")
        if args.get("no_subtitles"):
            cmd.append("--no-subtitles")
        if args.get("no_loudnorm"):
            cmd.append("--no-loudnorm")
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err), None

    if name == "grade":
        cmd = [
            str(HELPERS_DIR / "grade.py"),
            args["input_path"],
            "-o", args["output_path"],
        ]
        if args.get("filter"):
            cmd += ["--filter", args["filter"]]
        elif args.get("preset"):
            cmd += ["--preset", args["preset"]]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err), None

    if name == "bash":
        command = args["command"]
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
        return _format_result(proc.returncode, proc.stdout, proc.stderr), None

    if name == "read_file":
        path = Path(args["path"])
        if not path.exists():
            return f"File not found: {path}", None
        try:
            return path.read_text(), None
        except Exception as e:
            return f"Error reading file: {e}", None

    if name == "write_file":
        path = Path(args["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = "a" if args.get("append") else "w"
        try:
            with open(path, mode) as f:
                f.write(args["content"])
            return f"Written to {path}", None
        except Exception as e:
            return f"Error writing file: {e}", None

    return f"Unknown tool: {name}", None


# ---------------------------------------------------------------------------
# Session loop
# ---------------------------------------------------------------------------


def _build_image_message(img_path: Path) -> dict:
    b64 = base64.b64encode(img_path.read_bytes()).decode()
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": f"[Timeline view image: {img_path.name}]"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ],
    }


def run_session(
    videos_dir: Path,
    model: str,
    endpoint: str,
    max_turns: int,
) -> None:
    try:
        from openai import OpenAI
    except ImportError:
        sys.exit(
            "openai package not found.\n"
            "Install with:  pip install -e \".[copilot]\"\n"
            "or:            pip install openai"
        )

    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not github_token:
        sys.exit(
            "GITHUB_TOKEN is not set.\n"
            "Export a Personal Access Token with the 'copilot' scope:\n"
            "  export GITHUB_TOKEN=github_pat_..."
        )

    client = OpenAI(base_url=endpoint, api_key=github_token)

    edit_dir = videos_dir / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)

    # Build system prompt with working-directory context injected at the end
    system_prompt = load_skill_prompt()
    system_prompt += (
        f"\n\n## Session context\n\n"
        f"- Videos directory: `{videos_dir}`\n"
        f"- Edit directory: `{edit_dir}`\n"
        f"- Helpers directory: `{HELPERS_DIR}`\n"
        f"- All session outputs must go to `{edit_dir}/` (Hard Rule 12).\n"
    )

    messages: list[dict] = [{"role": "system", "content": system_prompt}]

    # Seed with prior session memory if available
    project_md = edit_dir / "project.md"
    if project_md.exists():
        prior = project_md.read_text().strip()
        if prior:
            messages.append({
                "role": "user",
                "content": (
                    f"[Prior session memory — project.md]\n\n{prior}\n\n---\n"
                    "I'm back. What should we pick up from or start fresh on?"
                ),
            })
            messages.append({
                "role": "assistant",
                "content": (
                    "I've reviewed the session notes above. Ready when you are — "
                    "just tell me what you'd like to work on."
                ),
            })

    print(f"\nvideo-use — GitHub Copilot orchestrator")
    print(f"  model:    {model}")
    print(f"  endpoint: {endpoint}")
    print(f"  videos:   {videos_dir}")
    print("Type your message. Enter 'exit' or press Ctrl+C to quit.\n")

    # Prompt for the first user message
    try:
        first_input = input("You: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nBye.")
        return

    if not first_input or first_input.lower() in ("exit", "quit", "q"):
        print("Bye.")
        return

    messages.append({"role": "user", "content": first_input})

    turn = 0
    while turn < max_turns:
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                max_tokens=4096,
            )
        except KeyboardInterrupt:
            print("\n[Interrupted]")
            break
        except Exception as e:
            print(f"\n[API error: {e}]")
            break

        choice = response.choices[0]
        message = choice.message

        # Serialize the assistant message back into the history
        msg_dict: dict = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]
        messages.append(msg_dict)

        if message.tool_calls:
            # Execute every requested tool call
            image_paths: list[Path] = []

            for tc in message.tool_calls:
                tool_name = tc.function.name
                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                # Pretty-print what we're doing
                args_preview = ", ".join(
                    f"{k}={v!r}" for k, v in list(tool_args.items())[:3]
                )
                print(f"  [tool] {tool_name}({args_preview})", flush=True)

                result_text, image_path = dispatch_tool(
                    tool_name, tool_args, videos_dir, edit_dir
                )

                # Truncate very long results so we don't blow the context window
                if len(result_text) > MAX_TOOL_RESULT_LENGTH:
                    result_text = result_text[:MAX_TOOL_RESULT_LENGTH] + "\n... [truncated]"

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_text,
                })

                if image_path and image_path.exists():
                    image_paths.append(image_path)

            # Inject timeline view images as user messages so vision-capable
            # models (gpt-4o, etc.) can reason about them
            for img_path in image_paths:
                messages.append(_build_image_message(img_path))

            turn += 1
            continue  # Let the model respond to the tool results

        # No tool calls — conversational turn
        if message.content:
            print(f"\nAssistant: {message.content}\n")

        if choice.finish_reason == "stop":
            try:
                user_input = input("You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nBye.")
                break

            if not user_input or user_input.lower() in ("exit", "quit", "q"):
                print("Bye.")
                break

            messages.append({"role": "user", "content": user_input})

        turn += 1

    if turn >= max_turns:
        print(f"\n[Reached max_turns={max_turns}. Session ended.]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="GitHub Copilot-backed video editing orchestrator for video-use.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Environment variables:\n"
            "  GITHUB_TOKEN       Personal Access Token with 'copilot' scope (required)\n"
            "  ELEVENLABS_API_KEY ElevenLabs API key for transcription (required for transcribe tools)\n"
            "\nModel options (via GitHub Copilot):\n"
            "  claude-opus-4-7    Default — Anthropic Claude Opus 4.7, strong reasoning + vision\n"
            "  claude-sonnet-4-5  Anthropic Claude Sonnet 4.5 — faster, lighter\n"
            "  gpt-4o             OpenAI GPT-4o — strong reasoning, vision support\n"
            "  gpt-4o-mini        OpenAI GPT-4o mini — fastest OpenAI option\n"
            "  o3-mini            OpenAI o3-mini — reasoning model\n"
            "\nAlternative endpoint (GitHub Models free tier):\n"
            "  --endpoint https://models.inference.ai.azure.com\n"
        ),
    )
    ap.add_argument(
        "videos_dir",
        type=Path,
        help="Directory containing the source video files.",
    )
    ap.add_argument(
        "--model",
        default="claude-opus-4-7",
        help="Model identifier for the Copilot API (default: claude-opus-4-7).",
    )
    ap.add_argument(
        "--endpoint",
        default="https://api.githubcopilot.com",
        help=(
            "GitHub Copilot API base URL "
            "(default: https://api.githubcopilot.com). "
            "Use https://models.inference.ai.azure.com for GitHub Models."
        ),
    )
    ap.add_argument(
        "--max-turns",
        type=int,
        default=100,
        help="Maximum LLM turns before the session ends (default: 100).",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"Not a directory: {videos_dir}")

    run_session(
        videos_dir=videos_dir,
        model=args.model,
        endpoint=args.endpoint,
        max_turns=args.max_turns,
    )


if __name__ == "__main__":
    main()
