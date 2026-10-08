"""Rank supplied recording candidates by normalized waveform correspondence."""

import argparse
from pathlib import Path
import numpy as np
from scipy.signal import correlate
from edit_io import run, load_json, save_json, resolve


# decode the first audio stream within a bounded comparison window
def decode(path, rate=8000, limit=600):
    if not 0 < limit <= 3600:
        raise ValueError("choose a bounded comparison window up to one hour")
    result = run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            path,
            "-t",
            str(limit),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            str(rate),
            "-f",
            "f32le",
            "pipe:1",
        ]
    )
    return np.frombuffer(result.stdout, "<f4").astype(float)


# find the strongest normalized waveform match despite gain and offset
def align(query, candidate, rate=8000):
    query = np.asarray(query, float)
    candidate = np.asarray(candidate, float)
    if (
        len(query) < 32
        or len(candidate) < len(query)
        or not np.isfinite(query).all()
        or not np.isfinite(candidate).all()
    ):
        raise ValueError("finite query must fit inside candidate")
    query = query - query.mean()
    energy = float(query @ query)
    if energy < 1e-12:
        raise ValueError("cannot align silent query")
    n = len(query)
    sums = np.concatenate(([0.0], np.cumsum(candidate)))
    squares = np.concatenate(([0.0], np.cumsum(candidate * candidate)))
    local = squares[n:] - squares[:-n] - (sums[n:] - sums[:-n]) ** 2 / n
    scores = correlate(candidate, query, mode="valid", method="fft") / np.sqrt(
        np.maximum(local, 1e-18) * energy
    )
    index = int(np.argmax(scores))
    return {
        "offset_samples": index,
        "offset_seconds": index / rate,
        "correlation": float(np.clip(scores[index], -1, 1)),
        "sample_rate": rate,
    }


# compare only the recordings explicitly listed in the supplied catalog
def rank(query, catalog_path):
    catalog_path = Path(catalog_path)
    rows = load_json(catalog_path)
    q = decode(query)
    results = []
    for row in rows:
        path = resolve(catalog_path.parent, row["file"])
        correspondence = align(q, decode(path))
        results.append(
            {
                "id": row["id"],
                "file": str(path),
                "title": row.get("title"),
                **correspondence,
            }
        )
    return {
        "candidates": sorted(results, key=lambda r: r["correlation"], reverse=True),
        "limit": "Candidate correspondence is not global song recognition or proof of source rights; verify two independent windows and recording version",
    }


# preserve query catalog and candidate recordings when writing the ranking
def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("query")
    p.add_argument("catalog")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    output = Path(a.out).resolve()
    catalog = Path(a.catalog).resolve()
    inputs = [Path(a.query).resolve(), catalog]
    inputs.extend(resolve(catalog.parent, row["file"]) for row in load_json(catalog))
    if any(
        output == path or (output.exists() and path.exists() and output.samefile(path))
        for path in inputs
    ):
        p.error("output would overwrite an input")
    save_json(a.out, rank(a.query, a.catalog))


if __name__ == "__main__":
    main()
