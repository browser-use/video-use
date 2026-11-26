#!/usr/bin/env python3
"""Session capture: real Claude Code work, cut to an allowlist, rendered as a still for B-roll.

    python3 helpers/session_capture.py spec.json out_dir/

The spec names one session .jsonl and the turns to show:

    {"session": "~/.claude/projects/<project>/<id>.jsonl",
     "title": "Claude Code",
     "user_label": "Chris",
     "keep": [{"at": "2026-11-16T16:48:58", "role": "user",
               "excerpts": ["Can you still do it locally?"]}]}

Each kept turn is matched by timestamp prefix and role, and each excerpt must appear verbatim in
that turn, so a capture can trim real work but never invent it. Only the excerpts are written to
`capture.jsonl`, in bare records (role, text, timestamp) with no cwd, session id or tool output.
The privacy scan (`session_scan.py`) runs on that file and stops the capture on any hit. Then
claude-replay (MIT, github.com/es617/claude-replay, pinned below) renders it offline, and
`capture.js` screenshots it at device scale 2 so a 2x camera push at 4K stays sharp.
"""
import json
import os
import shutil
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session_scan  # noqa: E402

REPLAY = "claude-replay@0.11.0"
REPLAY_DIR = os.path.expanduser("~/.cache/serious-ai-video-edit/claude-replay")
REPLAY_BIN = os.path.join(REPLAY_DIR, "node_modules", "claude-replay", "bin", "claude-replay.mjs")
NONET = """(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (remote unix-socket))
"""


def turn_text(rec):
    c = (rec.get("message") or {}).get("content")
    if isinstance(c, str):
        return c
    return "\n".join(p.get("text", "") for p in (c or []) if isinstance(p, dict) and p.get("type") == "text")


def build_capture(spec):
    """Return bare records for the kept excerpts. Raises ValueError if an excerpt isn't verbatim."""
    with open(os.path.expanduser(spec["session"]), encoding="utf-8") as f:
        rows = [json.loads(line) for line in f if line.strip()]
    out, parent = [], None
    for k in spec["keep"]:
        cands = [r for r in rows if r.get("type") == k["role"] and r.get("timestamp", "").startswith(k["at"]) and turn_text(r)]
        if not cands:
            raise ValueError(f"no {k['role']} turn with text at {k['at']}")
        src = "\n".join(turn_text(r) for r in cands)
        for e in k["excerpts"]:
            if e not in src:
                raise ValueError(f"excerpt not verbatim in the {k['at']} turn: {e[:60]!r}")
        u = str(uuid.uuid5(uuid.NAMESPACE_URL, k["at"] + k["role"]))
        out.append({"type": k["role"], "uuid": u, "parentUuid": parent, "timestamp": cands[0]["timestamp"],
                    "sessionId": "capture", "message": {"role": k["role"], "content": [{"type": "text", "text": "\n\n".join(k["excerpts"])}]}})
        parent = u
    return out


def main(argv):
    spec_path, out_dir = argv[0], argv[1]
    spec = json.load(open(spec_path, encoding="utf-8"))
    os.makedirs(out_dir, exist_ok=True)
    recs = build_capture(spec)
    cap = os.path.join(out_dir, "capture.jsonl")
    with open(cap, "w", encoding="utf-8") as f:
        for r in recs:
            f.write(json.dumps(r) + "\n")
    deny = session_scan.load_denylist(spec.get("denylist") and os.path.expanduser(spec["denylist"]) or os.environ.get("BROLL_DENYLIST"))
    hits = [h for t in session_scan.texts_from_jsonl(cap) + [spec.get("title", "")] for h in session_scan.scan_text(t, deny)]
    if hits:
        for kind, m in hits:
            print(f"privacy scan: {kind}: {m}", file=sys.stderr)
        sys.exit("capture stopped: trim the excerpts and run again")
    if not deny:
        print("warning: no denylist, client and prospect names were not checked", file=sys.stderr)
    html = os.path.join(out_dir, "replay.html")
    if not os.path.exists(REPLAY_BIN):  # one networked install of the pinned version; every render after it is offline
        subprocess.run(["npm", "install", "--silent", "--prefix", REPLAY_DIR, REPLAY], check=True)
    cmd = ["node", REPLAY_BIN, cap, "-o", html, "--no-tool-calls", "--no-thinking", "--font-size", "large",
           "--theme-file", os.path.join(HERE, "broll", "claude-dark.json"), "--title", spec.get("title", "Claude Code"),
           "--user-label", spec.get("user_label", "User"), "--assistant-label", "Claude", "--timing", "paced"]
    sb = os.path.join(out_dir, "nonet.sb")
    open(sb, "w").write(NONET)
    if shutil.which("sandbox-exec") and not os.environ.get("CAPTURE_ALLOW_NET"):
        cmd = ["sandbox-exec", "-f", sb] + cmd
    subprocess.run(cmd, check=True)
    subprocess.run(["node", os.path.join(HERE, "broll", "capture.js"), html, out_dir, str(spec.get("width", 1600)), str(spec.get("height", 1000))], check=True)
    print(f"capture ready in {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1:])
