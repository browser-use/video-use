"""Select editing instructions and record exactly which files were supplied."""

import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
CARDS = {
    "cuts": "references/context/cuts.md",
    "speech": "references/context/speech.md",
    "captions": "references/context/captions.md",
    "color": "references/context/color.md",
    "animation": "references/context/animation.md",
    "sound": "references/context/sound.md",
}


# Infer guidance from declared operations rather than guessing from English keywords.
def edl_needs(edl):
    if not isinstance(edl, dict):
        raise ValueError("EDL must be a JSON object")
    version = edl.get("version", 1)
    if isinstance(version, bool) or version != 1:
        raise ValueError("EDL routing supports version 1; select guidance with --need for other formats")
    for key in ("ranges", "overlays"):
        if key in edl and not isinstance(edl[key], list):
            raise ValueError(f"EDL {key} must be an array")
    selected = set()
    if edl.get("ranges"):
        selected.add("cuts")
    if edl.get("subtitles"):
        selected.add("captions")
    grade = edl.get("grade")
    if grade is not None and not isinstance(grade, str):
        raise ValueError("EDL grade must be a string")
    if grade and grade.strip().casefold() != "none":
        selected.add("color")
    if edl.get("overlays"):
        selected.add("animation")
    return selected


# Keep the shared contract mandatory and choose optional guidance in a stable order.
def select_context(needs=(), edl=None, *, root=ROOT, max_bytes=None):
    root = Path(root).resolve(strict=True)
    requested = set(needs)
    unknown = requested - CARDS.keys()
    if unknown:
        raise ValueError("Unknown guidance: " + ", ".join(sorted(unknown)))
    inferred = edl_needs(edl) if edl is not None else set()
    selected = requested | inferred
    paths = [("core", "SKILL.md")] + [(key, value) for key, value in CARDS.items() if key in selected]
    files = []
    for name, relative in paths:
        path = (root / relative).resolve(strict=True)
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"Instruction file must stay inside the skill: {relative}")
        data = path.read_bytes()
        # Reject undecodable guidance before writing a receipt or printing a partial bundle.
        data.decode("utf-8")
        reasons = ["always required"] if name == "core" else []
        if name in requested:
            reasons.append("requested capability")
        if name in inferred:
            reasons.append("declared EDL operation")
        files.append({"id": name, "path": relative, "bytes": len(data),
                      "sha256": hashlib.sha256(data).hexdigest(), "reasons": reasons})
    total = sum(item["bytes"] for item in files)
    if max_bytes is not None:
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        if total > max_bytes:
            raise ValueError(f"Selected guidance needs {total} bytes, above the {max_bytes} byte limit; no required instructions were dropped")
    return {"version": 1, "files": files, "total_bytes": total}


# Check recorded contents again before joining them so a receipt cannot describe stale text.
def read_bundle(receipt, *, root=ROOT):
    root = Path(root).resolve(strict=True)
    parts = []
    for item in receipt["files"]:
        path = (root / item["path"]).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError("Instruction path leaves the skill directory")
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"Instructions changed during selection: {item['path']}; select again")
        parts.append(f"<!-- {item['path']} -->\n" + data.decode("utf-8"))
    return "\n\n".join(parts)


# Print paths or the selected text and optionally retain an exclusive receipt for the run.
def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--need", action="append", choices=tuple(CARDS), default=[],
                        help="Capability needed by this task; repeat to combine guidance")
    parser.add_argument("--edl", type=Path, help="Add guidance implied by an existing EDL v1")
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--receipt", type=Path, help="Save a new JSON record under the project's edit directory")
    parser.add_argument("--max-bytes", type=int, help="Fail if selected instruction files exceed this budget")
    args = parser.parse_args(argv)
    try:
        edl = json.loads(args.edl.read_text(encoding="utf-8")) if args.edl else None
        if args.edl and not isinstance(edl, dict):
            raise ValueError("EDL must be a JSON object")
        receipt = select_context(args.need, edl, max_bytes=args.max_bytes)
        bundle = read_bundle(receipt)
        record = json.dumps(receipt, indent=2) + "\n"
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            with args.receipt.open("x", encoding="utf-8") as stream:
                stream.write(record)
        print(bundle if args.format == "markdown" else record, end="\n" if args.format == "markdown" else "")
    except (OSError, ValueError) as error:
        parser.exit(1, f"context: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
