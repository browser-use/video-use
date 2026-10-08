"""Small dependency-aware project context with explicit evidence and stale-state detection."""

import argparse
import json
from pathlib import Path
from edit_io import load_json, save_json, sha256, resolve

PHASES = {
    "brief",
    "sources",
    "story",
    "music",
    "timeline",
    "effects",
    "render",
    "review",
}


# check artifact metadata and reject missing or cyclic dependencies
def validate(data):
    """Check artifact metadata and reject missing or cyclic dependencies."""
    rows = data.setdefault("artifacts", {})
    visiting = set()
    visited = set()

    # traverse dependencies while distinguishing active visits from completed ones
    def visit(ident):
        """Traverse dependencies while distinguishing active visits from completed ones."""
        if ident in visiting:
            raise ValueError("context dependencies contain a cycle")
        if ident in visited:
            return
        if ident not in rows:
            raise ValueError(f"unknown context dependency {ident}")
        visiting.add(ident)
        for dep in rows[ident].get("depends_on", []):
            visit(dep)
        visiting.remove(ident)
        visited.add(ident)

    for ident, row in rows.items():
        if row.get("phase") not in PHASES:
            raise ValueError("unknown context phase")
        if row.get("status") not in ("draft", "measured", "reviewed", "blocked"):
            raise ValueError("unknown evidence status")
        if not row.get("path") or not row.get("summary"):
            raise ValueError("context needs an artifact path and a useful summary")
        if not isinstance(row.get("sha256"), str) or len(row["sha256"]) != 64:
            raise ValueError("context artifact needs a sha256 fingerprint")
        visit(ident)


# save an artifact fingerprint and the dependency versions it was built from
def record(context, ident, entry):
    """Save an artifact fingerprint and the dependency versions it was built from."""
    context = Path(context)
    data = load_json(context) if context.exists() else {"version": 1, "artifacts": {}}
    data.setdefault("artifacts", {})
    path = resolve(context.parent, entry["path"])
    if path == context.resolve() or (path.exists() and context.exists() and path.samefile(context)):
        raise ValueError("context cannot record itself as an artifact")
    if not path.is_file():
        raise FileNotFoundError(path)
    entry = dict(entry)
    entry["sha256"] = sha256(path)
    data["artifacts"][ident] = entry
    validate(data)
    entry["dependency_hashes"] = {
        dep: data["artifacts"][dep]["sha256"] for dep in entry.get("depends_on", [])
    }
    data["artifacts"][ident] = entry
    validate(data)
    save_json(context, data)


# report stale artifacts including changes inherited from dependencies
def view(context, phase=None):
    """Report stale artifacts including changes inherited from dependencies."""
    context = Path(context)
    data = load_json(context)
    validate(data)
    rows = data["artifacts"]
    states = {}

    # memoize whether an artifact or any of its dependencies has changed
    def stale(ident):
        """Memoize whether an artifact or any of its dependencies has changed."""
        if ident in states:
            return states[ident]
        row = rows[ident]
        path = resolve(context.parent, row["path"])
        changed = not path.is_file() or sha256(path) != row["sha256"]
        for dep in row.get("depends_on", []):
            changed = (
                stale(dep)
                or row.get("dependency_hashes", {}).get(dep) != rows[dep]["sha256"]
                or changed
            )
        states[ident] = changed
        return changed

    return {
        "artifacts": [
            {**row, "id": ident, "stale": stale(ident)}
            for ident, row in rows.items()
            if phase is None or row["phase"] == phase
        ],
        "instruction": "Load only the relevant artifact files; stale evidence must be regenerated before it can support a claim",
    }


# record an artifact or show evidence for a selected project phase
def main():
    """Record an artifact or show evidence for a selected project phase."""
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("record")
    r.add_argument("context")
    r.add_argument("id")
    r.add_argument("entry")
    s = sub.add_parser("show")
    s.add_argument("context")
    s.add_argument("--phase", choices=sorted(PHASES))
    a = p.parse_args()
    if a.command == "record":
        record(a.context, a.id, load_json(a.entry))
    else:
        print(json.dumps(view(a.context, a.phase), indent=2))


if __name__ == "__main__":
    main()
