"""Find visual correspondence candidates in independently acquired source footage."""

import argparse
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from source_scan import catalog, selected_frames
from edit_io import save_json


# score local image features that agree on one geometric transformation
def correspondence(query, candidate):
    """Score local image features that agree on one geometric transformation."""
    detector = cv2.SIFT_create(nfeatures=1000)
    qa, qd = detector.detectAndCompute(
        cv2.cvtColor(np.asarray(query), cv2.COLOR_RGB2GRAY), None
    )
    ca, cd = detector.detectAndCompute(
        cv2.cvtColor(np.asarray(candidate), cv2.COLOR_RGB2GRAY), None
    )
    if qd is None or cd is None or len(cd) < 2:
        return {"inliers": 0, "matches": 0, "reprojection_px": None}
    pairs = cv2.BFMatcher().knnMatch(qd, cd, k=2)
    matches = [
        p[0] for p in pairs if len(p) == 2 and p[0].distance < 0.72 * p[1].distance
    ]
    if len(matches) < 4:
        return {"inliers": 0, "matches": len(matches), "reprojection_px": None}
    a = np.float32([qa[m.queryIdx].pt for m in matches])
    b = np.float32([ca[m.trainIdx].pt for m in matches])
    matrix, mask = cv2.findHomography(a, b, cv2.RANSAC, 3)
    if matrix is None or mask is None:
        return {"inliers": 0, "matches": len(matches), "reprojection_px": None}
    projected = cv2.perspectiveTransform(a[:, None, :], matrix)[:, 0, :]
    valid = mask[:, 0].astype(bool)
    if not valid.any():
        return {"inliers": 0, "matches": len(matches), "reprojection_px": None}
    return {
        "inliers": int(valid.sum()),
        "matches": len(matches),
        "reprojection_px": float(
            np.linalg.norm(projected[valid] - b[valid], axis=1).mean()
        ),
        "homography": matrix.tolist(),
    }


# rank sampled source frames against a query image for later visual review
def search(query, source, every=1, limit=12):
    """Rank sampled source frames against a query image for later visual review."""
    if not math.isfinite(every) or every <= 0:
        raise ValueError("sampling interval must be positive")
    index = catalog(source)
    selected = []
    next_time = index["frames"][0]["pts"]
    for row in index["frames"]:
        if row["pts"] >= next_time:
            selected.append(row["frame"])
            next_time = row["pts"] + every
    with Image.open(query) as im:
        reference = im.convert("RGB")
        reference.thumbnail((640, 640))
    results = []
    for frame, image in selected_frames(source, selected, 640):
        results.append(
            {
                "frame": frame,
                "pts": index["frames"][frame]["pts"],
                **correspondence(reference, image),
            }
        )
    return {
        "source_sha256": index["sha256"],
        "candidates": sorted(
            results, key=lambda r: (-r["inliers"], r["reprojection_px"] if r["reprojection_px"] is not None else 1e9)
        )[:limit],
        "limit": "Visual candidates need native-frame and semantic review; coarse sampling cannot certify exact action timing",
    }


# search source footage while preventing the report from replacing an input
def main():
    """Search source footage while preventing the report from replacing an input."""
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("query")
    p.add_argument("source")
    p.add_argument("--every", type=float, default=1)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    if Path(a.out).resolve() in (Path(a.query).resolve(), Path(a.source).resolve()):
        p.error("output would overwrite input")
    if Path(a.out).exists() or Path(a.out).is_symlink():
        p.error("output already exists choose a new report path")
    save_json(a.out, search(a.query, a.source, a.every), exclusive=True)


if __name__ == "__main__":
    main()
