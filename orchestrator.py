#!/usr/bin/env python3
"""GitHub Copilot-backed video editing orchestrator for video-use.

Replaces the `claude` CLI runtime with a standalone Python script that drives
the same video editing pipeline using the GitHub Copilot SDK.  The SDK spawns
the Copilot CLI as a subprocess automatically — no separate CLI install needed.

All 12 hard production rules from SKILL.md are enforced via the system prompt.
No logic changes to the skill or helpers are required.

Requirements:
  pip install -e ".[copilot]"          # github-copilot-sdk + pydantic
  GITHUB_TOKEN=... in .env             # PAT with 'copilot' scope
    OR  run `copilot auth login` once  # sign in via browser (no token needed)
  ELEVENLABS_API_KEY=... in .env       # for transcription
  ffmpeg and ffprobe on PATH

Usage:
  python orchestrator.py /path/to/videos
  python orchestrator.py /path/to/videos --model claude-sonnet-4.5
  python orchestrator.py /path/to/videos --enable-shell
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

try:
    from copilot import CopilotClient, SubprocessConfig, define_tool
    from copilot.session import PermissionRequestResult
    from pydantic import BaseModel, Field
except ImportError:  # deferred error — shown at runtime with a friendly message
    CopilotClient = SubprocessConfig = define_tool = PermissionRequestResult = None  # type: ignore
    BaseModel = object  # type: ignore
    Field = lambda **_: None  # type: ignore

# ---------------------------------------------------------------------------
# Repo-relative paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent
HELPERS_DIR = REPO_ROOT / "helpers"
SKILL_MD = REPO_ROOT / "SKILL.md"


def _load_env_file() -> None:
    """Load key=value pairs from .env into os.environ (does not overwrite existing vars)."""
    for candidate in [REPO_ROOT / ".env", Path(".env")]:
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in os.environ:
                    os.environ[k] = v
            break


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
MAX_TOOL_RESULT_LENGTH = 20_000

# Maximum image size (bytes) to embed; larger images are downscaled first.
MAX_IMAGE_BYTES = 1_500_000  # 1.5 MB


# ---------------------------------------------------------------------------
# Path sandbox helper
# ---------------------------------------------------------------------------


def _is_under(path: Path, parent: Path) -> bool:
    """Return True if *path* is the same as or nested under *parent*."""
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Helpers runner
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
    result = "\n".join(parts)
    if len(result) > MAX_TOOL_RESULT_LENGTH:
        result = result[:MAX_TOOL_RESULT_LENGTH] + "\n... [truncated]"
    return result


# ---------------------------------------------------------------------------
# Image attachment helper
# ---------------------------------------------------------------------------


def _prepare_image_attachment(img_path: Path) -> dict:
    """Return a blob attachment dict, downscaling via ffmpeg if > MAX_IMAGE_BYTES."""
    raw = img_path.read_bytes()
    mime = "image/png"

    if len(raw) > MAX_IMAGE_BYTES:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-i", str(img_path),
                    "-vf", "scale='min(960,iw)':-2",
                    str(tmp_path),
                ],
                capture_output=True,
                check=False,
            )
            raw = tmp_path.read_bytes()
            mime = "image/jpeg"
        except Exception:
            pass
        finally:
            tmp_path.unlink(missing_ok=True)

    if len(raw) > MAX_IMAGE_BYTES:
        return {}  # too large — caller will skip attachment

    return {
        "type": "blob",
        "data": base64.b64encode(raw).decode(),
        "mimeType": mime,
    }


# ---------------------------------------------------------------------------
# Tool parameter models (module-level so get_type_hints() can resolve them)
# ---------------------------------------------------------------------------


class TranscribeParams(BaseModel):
    video_path: str = Field(description="Absolute path to the video file.")
    language: Optional[str] = Field(default=None, description="ISO language code (e.g. 'en'). Omit to auto-detect.")
    num_speakers: Optional[int] = Field(default=None, description="Number of speakers for diarization.")


class TranscribeBatchParams(BaseModel):
    workers: Optional[int] = Field(default=None, description="Parallel workers (default 4).")
    num_speakers: Optional[int] = Field(default=None, description="Number of speakers (optional).")


class PackTranscriptsParams(BaseModel):
    silence_threshold: Optional[float] = Field(default=None, description="Silence gap in seconds that triggers a phrase break (default 0.5).")


class TimelineViewParams(BaseModel):
    video_path: str = Field(description="Absolute path to the video file.")
    start: float = Field(description="Start time in seconds.")
    end: float = Field(description="End time in seconds.")
    n_frames: Optional[int] = Field(default=None, description="Number of filmstrip frames to extract (default 8).")
    transcript_path: Optional[str] = Field(default=None, description="Optional path to transcript JSON for word label overlay.")


class RenderParams(BaseModel):
    edl_path: str = Field(description="Absolute path to edl.json.")
    output_path: str = Field(description="Output video path (e.g. edit/final.mp4).")
    preview: Optional[bool] = Field(default=None, description="Preview mode: 1080p, CRF 22, faster encode.")
    build_subtitles: Optional[bool] = Field(default=None, description="Build master.srt from transcripts + EDL timeline offsets.")
    no_subtitles: Optional[bool] = Field(default=None, description="Skip subtitles even if the EDL references one.")
    no_loudnorm: Optional[bool] = Field(default=None, description="Skip audio loudness normalization.")


class GradeParams(BaseModel):
    input_path: str = Field(description="Input video path.")
    output_path: str = Field(description="Output video path.")
    preset: Optional[str] = Field(default=None, description="Grade preset: subtle, neutral_punch, warm_cinematic, none.")
    filter: Optional[str] = Field(default=None, description="Raw ffmpeg filter string (overrides preset).")


# ---------------------------------------------------------------------------
# Session loop
# ---------------------------------------------------------------------------


async def run_session(
    videos_dir: Path,
    model: str,
    enable_shell: bool,
    max_turns: int,
) -> None:
    if CopilotClient is None:
        sys.exit(
            "Required packages not found.\n"
            "Install with:  pip install -e \".[copilot]\"\n"
            "or:            pip install github-copilot-sdk pydantic"
        )

    _load_env_file()

    edit_dir = videos_dir / "edit"
    edit_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    @define_tool(
        description=(
            "Transcribe a single video with ElevenLabs Scribe. "
            "Writes word-level transcript JSON to edit/transcripts/<stem>.json. "
            "Cached — skips upload if the JSON already exists."
        ),
        skip_permission=True,
    )
    async def transcribe(params: TranscribeParams) -> str:
        cmd = [str(HELPERS_DIR / "transcribe.py"), params.video_path]
        cmd += ["--edit-dir", str(edit_dir)]
        if params.language:
            cmd += ["--language", params.language]
        if params.num_speakers:
            cmd += ["--num-speakers", str(params.num_speakers)]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err)

    @define_tool(
        description=(
            "Batch-transcribe every video in the session videos directory using parallel workers. "
            "Cached per source — already-transcribed files are skipped."
        ),
        skip_permission=True,
    )
    async def transcribe_batch(params: TranscribeBatchParams) -> str:
        # Always use the session videos_dir — model cannot redirect this elsewhere
        cmd = [str(HELPERS_DIR / "transcribe_batch.py"), str(videos_dir)]
        cmd += ["--edit-dir", str(edit_dir)]
        if params.workers:
            cmd += ["--workers", str(params.workers)]
        if params.num_speakers:
            cmd += ["--num-speakers", str(params.num_speakers)]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err)

    @define_tool(
        description=(
            "Pack all per-source transcript JSONs in edit/transcripts/ into "
            "takes_packed.md — the primary phrase-level reading surface for cut decisions."
        ),
        skip_permission=True,
    )
    async def pack_transcripts(params: PackTranscriptsParams) -> str:
        cmd = [str(HELPERS_DIR / "pack_transcripts.py"), "--edit-dir", str(edit_dir)]
        if params.silence_threshold is not None:
            cmd += ["--silence-threshold", str(params.silence_threshold)]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err)

    # Side-channel for the last timeline image path so it can be attached in the
    # next user message (the SDK attachment API goes on session.send, not tool results).
    _pending_images: list[Path] = []

    @define_tool(
        description=(
            "Generate a filmstrip + waveform PNG for a time range of a video. "
            "Use at decision points (ambiguous pauses, retake comparison, cut-point "
            "sanity checks). NOT a scan tool — call only when you need a visual check."
        ),
        skip_permission=True,
    )
    async def timeline_view(params: TimelineViewParams) -> str:
        video_path = Path(params.video_path)
        verify_dir = edit_dir / "verify"
        verify_dir.mkdir(parents=True, exist_ok=True)
        out_img = verify_dir / f"timeline_{video_path.stem}_{params.start:.2f}_{params.end:.2f}.png"
        cmd = [
            str(HELPERS_DIR / "timeline_view.py"),
            str(video_path),
            str(params.start),
            str(params.end),
            "-o", str(out_img),
        ]
        if params.n_frames:
            cmd += ["--n-frames", str(params.n_frames)]
        if params.transcript_path:
            cmd += ["--transcript", params.transcript_path]
        rc, out, err = _run_helper(cmd)
        result = _format_result(rc, out, err)
        if rc == 0 and out_img.exists():
            _pending_images.append(out_img)
            result += f"\nImage saved to: {out_img} (will be attached to your next reply)"
        return result

    @define_tool(
        description=(
            "Render a video from an EDL (edit decision list JSON). "
            "Runs the full pipeline: per-segment extract with grade + 30ms audio fades → "
            "lossless concat → overlays → subtitles LAST → loudnorm."
        ),
        skip_permission=True,
    )
    async def render(params: RenderParams) -> str:
        cmd = [
            str(HELPERS_DIR / "render.py"),
            params.edl_path,
            "-o", params.output_path,
        ]
        if params.preview:
            cmd.append("--preview")
        if params.build_subtitles:
            cmd.append("--build-subtitles")
        if params.no_subtitles:
            cmd.append("--no-subtitles")
        if params.no_loudnorm:
            cmd.append("--no-loudnorm")
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err)

    @define_tool(
        description=(
            "Apply a color grade to a video via ffmpeg filter chain. "
            "Presets: subtle, neutral_punch, warm_cinematic, none. "
            "Omit both preset and filter for auto mode (data-driven per-clip correction)."
        ),
        skip_permission=True,
    )
    async def grade(params: GradeParams) -> str:
        cmd = [
            str(HELPERS_DIR / "grade.py"),
            params.input_path,
            "-o", params.output_path,
        ]
        if params.filter:
            cmd += ["--filter", params.filter]
        elif params.preset:
            cmd += ["--preset", params.preset]
        rc, out, err = _run_helper(cmd)
        return _format_result(rc, out, err)

    # ------------------------------------------------------------------
    # Permission handler — sandboxes file writes to edit_dir; shell off by default
    # ------------------------------------------------------------------

    def on_permission_request(request, invocation) -> "PermissionRequestResult":
        kind = request.kind.value if hasattr(request.kind, "value") else str(request.kind)

        if kind == "shell" and not enable_shell:
            print(
                "\n[shell tool blocked — restart with --enable-shell to allow shell commands]",
                flush=True,
            )
            return PermissionRequestResult(kind="denied-interactively-by-user")

        if kind == "write":
            file_name = getattr(request, "file_name", None) or ""
            if file_name and not _is_under(Path(file_name), edit_dir):
                print(f"\n[write blocked — path outside edit_dir: {file_name}]", flush=True)
                return PermissionRequestResult(kind="denied-by-rules")

        return PermissionRequestResult(kind="approved")

    # ------------------------------------------------------------------
    # User input handler (enables ask_user tool in the CLI)
    # ------------------------------------------------------------------

    async def on_user_input_request(request, invocation) -> dict:
        question = request.get("question", "")
        choices = request.get("choices")
        print(f"\nAssistant asks: {question}")
        if choices:
            for i, c in enumerate(choices, 1):
                print(f"  {i}. {c}")
        try:
            answer = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("Your answer: ").strip()
            )
        except (EOFError, KeyboardInterrupt):
            answer = ""
        return {"answer": answer, "wasFreeform": True}

    # ------------------------------------------------------------------
    # Build system prompt
    # ------------------------------------------------------------------

    system_content = (
        load_skill_prompt()
        + f"\n\n## Session context\n\n"
        f"- Videos directory: `{videos_dir}`\n"
        f"- Edit directory: `{edit_dir}`\n"
        f"- Helpers directory: `{HELPERS_DIR}`\n"
        f"- All session outputs must go to `{edit_dir}/` (Hard Rule 12).\n"
    )

    # ------------------------------------------------------------------
    # Print banner
    # ------------------------------------------------------------------

    print(f"\nvideo-use — GitHub Copilot SDK orchestrator")
    print(f"  model:    {model or 'auto (Copilot selects)'}")
    print(f"  videos:   {videos_dir}")
    print(f"  shell:    {'enabled' if enable_shell else 'disabled  (--enable-shell to allow)'}")
    print("Type your message. Enter 'exit' or press Ctrl+C to quit.\n")

    # ------------------------------------------------------------------
    # Prior session memory
    # ------------------------------------------------------------------

    project_md = edit_dir / "project.md"
    initial_context: str | None = None
    if project_md.exists():
        prior = project_md.read_text().strip()
        if prior:
            initial_context = (
                f"[Prior session memory — project.md]\n\n{prior}\n\n---\n"
                "I'm back. What should we pick up from or start fresh on?"
            )

    # ------------------------------------------------------------------
    # SDK client + session
    # ------------------------------------------------------------------

    github_token = os.environ.get("GITHUB_TOKEN", "").strip() or None
    config = SubprocessConfig(
        cwd=str(videos_dir),
        github_token=github_token,
    )

    session_kwargs: dict = dict(
        on_permission_request=on_permission_request,
        on_user_input_request=on_user_input_request,
        tools=[transcribe, transcribe_batch, pack_transcripts, timeline_view, render, grade],
        system_message={"content": system_content},
        streaming=True,
    )
    if model:
        session_kwargs["model"] = model

    try:
        from copilot.generated.session_events import (
            AssistantMessageData,
            AssistantMessageDeltaData,
            SessionIdleData,
        )
    except ImportError:
        # Older SDK versions may use a different import path
        from copilot.session_events import (  # type: ignore[no-redef]
            AssistantMessageData,
            AssistantMessageDeltaData,
            SessionIdleData,
        )

    async with CopilotClient(config) as client:
        async with await client.create_session(**session_kwargs) as session:

            # Seed prior session memory as first user turn
            if initial_context:
                seed_done = asyncio.Event()

                def _on_seed(event):
                    match event.data:
                        case AssistantMessageData():
                            seed_done.set()
                        case SessionIdleData():
                            seed_done.set()

                unsub_seed = session.on(_on_seed)
                await session.send(initial_context)
                await seed_done.wait()
                unsub_seed()
                print()

            turn = 0
            while turn < max_turns:
                # Collect any pending timeline images
                attachments: list[dict] = []
                while _pending_images:
                    img_path = _pending_images.pop(0)
                    if img_path.exists():
                        att = _prepare_image_attachment(img_path)
                        if att:
                            attachments.append(att)

                # Prompt user
                try:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("You: ").strip()
                    )
                except (EOFError, KeyboardInterrupt):
                    print("\nBye.")
                    break

                if not user_input or user_input.lower() in ("exit", "quit", "q"):
                    print("Bye.")
                    break

                # Wait for full response
                response_done = asyncio.Event()
                print("\nAssistant: ", end="", flush=True)

                def on_event(event):
                    match event.data:
                        case AssistantMessageDeltaData() as data:
                            delta = data.delta_content or ""
                            print(delta, end="", flush=True)
                        case AssistantMessageData():
                            print()  # ensure newline after full message
                        case SessionIdleData():
                            response_done.set()

                unsub = session.on(on_event)
                send_kwargs: dict = {"prompt": user_input}
                if attachments:
                    send_kwargs["attachments"] = attachments

                await session.send(**send_kwargs)
                await response_done.wait()
                unsub()
                print()

                turn += 1

    if turn >= max_turns:
        print(f"\n[Reached max_turns={max_turns}. Session ended.]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="GitHub Copilot SDK video editing orchestrator for video-use.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Authentication (pick one):\n"
            "  copilot auth login              Sign in via browser — no token needed\n"
            "  GITHUB_TOKEN=... in .env        PAT with 'copilot' scope\n"
            "    https://github.com/settings/tokens\n"
            "\nModel options (via GitHub Copilot CLI — use /model inside session to switch):\n"
            "  (omit --model)                  Copilot auto-selects the best model\n"
            "  claude-opus-4.5                 Anthropic Claude Opus 4.5 — complex tasks\n"
            "  claude-sonnet-4.5               Anthropic Claude Sonnet 4.5 — faster\n"
            "  gpt-5                           OpenAI GPT-5\n"
            "  gpt-4.1                         OpenAI GPT-4.1\n"
            "\nEnvironment variables:\n"
            "  GITHUB_TOKEN        PAT with 'copilot' scope (alternative to browser login)\n"
            "  ELEVENLABS_API_KEY  ElevenLabs API key for transcription\n"
        ),
    )
    ap.add_argument(
        "videos_dir",
        type=Path,
        help="Directory containing the source video files.",
    )
    ap.add_argument(
        "--model",
        default="",
        help="Model identifier (default: Copilot auto-selects). Use /model inside session to switch.",
    )
    ap.add_argument(
        "--enable-shell",
        action="store_true",
        default=False,
        help=(
            "Enable the built-in shell tool (disabled by default). "
            "Only enable when you trust the model and understand the security implications."
        ),
    )
    ap.add_argument(
        "--max-turns",
        type=int,
        default=200,
        help="Maximum interactive turns before the session ends (default: 200).",
    )
    args = ap.parse_args()

    videos_dir = args.videos_dir.resolve()
    if not videos_dir.is_dir():
        sys.exit(f"Not a directory: {videos_dir}")

    asyncio.run(
        run_session(
            videos_dir=videos_dir,
            model=args.model,
            enable_shell=args.enable_shell,
            max_turns=args.max_turns,
        )
    )


if __name__ == "__main__":
    main()
