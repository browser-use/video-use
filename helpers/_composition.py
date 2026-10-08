"""Render the explicit manifest: prepare shots, copy-concat, captions last, separate audio mux."""

import subprocess
from pathlib import Path
import numpy as np
from PIL import Image
from cards import CardRenderer, mask_at, mask_paths, font_path
from cut_list import validate
from edit_io import load_json, save_json, run, probe, source_path, sha256, resolve
from mix_audio import build as mix_audio


# prepare one picture interval with exact frame count and optional isolation
def stage_shot(manifest, root, shot, path):
    width, height = manifest["picture"][2:]
    count = shot["end_frame"] - shot["start_frame"]
    if shot.get("time_map") is not None:
        from effects import stage_mapped

        stage_mapped(manifest, root, shot, path)
        return
    base = ["ffmpeg", "-v", "error", "-y", "-threads", "2"]
    if shot.get("source") is None:
        base += ["-f", "lavfi", "-i", f"color=c=black:s={width}x{height}:r=30"]
    else:
        base += ["-i", str(source_path(manifest, root, shot["source"]))]
    trim = (
        [f'trim=start_frame={shot["source_frame"]}']
        if shot.get("source_frame") is not None
        else ["setpts=PTS-STARTPTS", f'trim=start={float(shot.get("source_start",0))}']
    )
    filters = trim + [
        f'setpts=(PTS-STARTPTS)/{float(shot.get("speed",1))}',
        "fps=30",
        f"scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos",
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black",
        "setsar=1",
    ]
    if shot.get("source_crop"):
        cx, cy, cw, ch = shot["source_crop"]
        filters.insert(len(trim) + 2, f"crop={cw}:{ch}:{cx}:{cy}")
    if shot.get("grade"):
        grade = shot["grade"]
        if set(grade) - {"contrast", "saturation", "gamma"}:
            raise ValueError("unsupported grade operation")
        filters += ["eq=" + ":".join(f"{k}={float(v)}" for k, v in grade.items())]
    encode = [
        "-an",
        "-c:v",
        "libx264",
        "-crf",
        "0",
        "-preset",
        "fast",
        "-threads",
        "2",
        "-pix_fmt",
        "yuv420p",
        "-video_track_timescale",
        "15360",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        str(path),
    ]
    if not shot.get("isolation_mask"):
        run(
            base
            + [
                "-map",
                "0:v:0",
                "-sn",
                "-dn",
                "-vf",
                ",".join(filters),
                "-frames:v",
                str(count),
            ]
            + encode,
            log=path.with_suffix(".log"),
        )
    else:
        # Matte coordinates address the already fitted picture, on the global frame clock.
        decode = base + [
            "-map",
            "0:v:0",
            "-vf",
            ",".join(filters),
            "-frames:v",
            str(count),
            "-an",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
        with path.with_suffix(".decode.log").open("wb") as dl, path.with_suffix(
            ".log"
        ).open("wb") as el:
            src = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=dl)
            enc = subprocess.Popen(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-video_size",
                    f"{width}x{height}",
                    "-framerate",
                    "30",
                    "-i",
                    "pipe:0",
                ]
                + encode,
                stdin=subprocess.PIPE,
                stderr=el,
            )
            try:
                for i in range(count):
                    raw = src.stdout.read(width * height * 3)
                    if len(raw) != width * height * 3:
                        raise ValueError("source ended during isolated shot")
                    frame = np.frombuffer(raw, np.uint8).reshape(height, width, 3)
                    mask = (
                        np.asarray(
                            mask_at(
                                shot["isolation_mask"],
                                shot["start_frame"] + i,
                                (width, height),
                                root,
                            ),
                            np.float32,
                        )
                        / 255
                    )
                    enc.stdin.write(
                        np.round(frame * mask[:, :, None]).astype(np.uint8).tobytes()
                    )
                enc.stdin.close()
                if src.wait() != 0 or enc.wait() != 0:
                    raise RuntimeError("isolated shot render failed")
            except BaseException:
                src.kill()
                enc.kill()
                src.wait()
                enc.wait()
                raise
    video = next(s for s in probe(path, True)["streams"] if s["codec_type"] == "video")
    if int(video["nb_read_frames"]) != count:
        raise ValueError(f'{shot["id"]}: source cannot fill the planned shot')


# collect declared input paths before creating any generated files
def input_paths(manifest, root):
    paths = {resolve(root, s["file"]) for s in manifest["sources"].values()}
    paths.update(resolve(root, p) for p in manifest.get("study_media", []))
    paths.update(Path(font_path(manifest, root, k)) for k in manifest.get("fonts", {}))
    for rows, key in (
        (manifest["shots"], "isolation_mask"),
        (manifest.get("cards", []), "occlusion_mask"),
        (manifest.get("layers", []), "mask"),
    ):
        for row in rows:
            if key in row:
                paths.update(
                    mask_paths(row[key], row["start_frame"], row["end_frame"], root)
                )
            if "image" in row:
                paths.add(resolve(root, row["image"]))
    return paths


# stage picture and audio then encode and verify the final composition
def build(manifest_path, out):
    manifest_path = Path(manifest_path).resolve()
    root = manifest_path.parent
    m = load_json(manifest_path)
    validate(m, root)
    if not m.get("audio"):
        raise ValueError(
            "composition delivery currently requires declared non silent audio"
        )
    out = Path(out)
    if out.is_symlink():
        raise FileExistsError("output must not be a symbolic link")
    out = out.resolve()
    if out.exists():
        raise FileExistsError(f"{out}; choose a new versioned output")
    work = out.parent / (out.stem + "_build")
    paths = input_paths(m, root) | {manifest_path}
    if out in paths or any(p.is_relative_to(work) for p in paths):
        raise ValueError(
            "render inputs must be outside the output and generated build directory"
        )
    if work.exists() or work.is_symlink():
        raise FileExistsError(
            f"{work}; choose a new versioned output and build directory"
        )
    work.mkdir(parents=True)
    provenance = {
        k: {
            "file": str(source_path(m, root, k)),
            "sha256": sha256(source_path(m, root, k)),
        }
        for k, v in m["sources"].items()
        if not v.get("study_only")
    }
    save_json(work / "source_hashes.json", provenance)
    save_json(work / "manifest.json", m)
    paths = []
    for i, shot in enumerate(m["shots"]):
        path = work / f"shot_{i+1:03}.mp4"
        stage_shot(m, root, shot, path)
        paths.append(path)
    # Relative controlled filenames avoid concat quoting hazards.
    concat = work / "concat.txt"
    concat.write_text(
        "".join(
            f"file '{p.name}'\nduration {(shot['end_frame']-shot['start_frame'])/30:.12f}\n"
            for p, shot in zip(paths, m["shots"])
        )
    )
    picture = work / "picture.mp4"
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat,
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "copy",
            picture,
        ]
    )
    if m.get("layers") or any(s.get("effects") for s in m["shots"]):
        from effects import render_layers

        staged = {}
        for i, layer in enumerate(m.get("layers", [])):
            if "source" not in layer:
                continue
            path = work / f"layer_{i+1:03}.mp4"
            stage_shot(m, root, layer, path)
            staged[layer["id"]] = path
        treated = work / "treated.mkv"
        render_layers(m, root, picture, staged, treated)
        picture = treated
    captions = work / "captions.mov"
    CardRenderer(m, root).write_movie(captions)
    width, height = m["canvas"]
    x, y, _, _ = m["picture"]
    final_picture = work / "final_picture.mp4"
    graph = (
        f"[0:v]settb=1/30,setpts=N,pad={width}:{height}:{x}:{y}:black[base];"
        f"[1:v]settb=1/30,setpts=N[text];[base][text]overlay={x}:{y}:shortest=1:repeatlast=0:format=auto,format=yuv420p[v]"
    )
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-threads",
            "2",
            "-i",
            picture,
            "-i",
            captions,
            "-filter_complex_threads",
            "2",
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-an",
            "-frames:v",
            str(m["total_frames"]),
            "-r",
            "30",
            "-fps_mode",
            "cfr",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-preset",
            "medium",
            "-threads",
            "2",
            "-pix_fmt",
            "yuv420p",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-movflags",
            "+faststart",
            final_picture,
        ],
        log=work / "picture_render.log",
    )
    master = mix_audio(m, root, work / "audio")
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-i",
            final_picture,
            "-i",
            master,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "320k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-t",
            f'{m["total_frames"]/30:.12f}',
            "-map_metadata",
            "-1",
            "-movflags",
            "+faststart",
            out,
        ],
        log=work / "mux.log",
    )
    from verify_edit import verify, review_sheets

    report = verify(m, root, out, work)
    report["review_sheets"] = review_sheets(m, out, work / "review")
    save_json(work / "verification.json", report)
    if not report["technical_pass"]:
        raise RuntimeError(
            f"final verification failed; inspect {work}/verification.json"
        )
    return report
