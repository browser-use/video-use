"""Find visual correspondence candidates in independently acquired source footage."""

import argparse
import math
from pathlib import Path
import cv2
import numpy as np
from PIL import Image
from source_scan import catalog, selected_frames
from edit_io import file_state, load_json, save_json, sha256


# score local image features that agree on one geometric transformation
def correspondence(query, candidate, *, detector=None, query_features=None):
    """Score local image features that agree on one geometric transformation."""
    if detector is None:
        detector = cv2.SIFT_create(nfeatures=1000)
    qa, qd = query_features if query_features is not None else detector.detectAndCompute(
        cv2.cvtColor(np.asarray(query), cv2.COLOR_RGB2GRAY), None)
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
def search(query, source, every=1, limit=12, index=None):
    """Rank sampled source frames against a query image for later visual review."""
    if type(every) not in (int, float) or not math.isfinite(every) or every <= 0:
        raise ValueError("sampling interval must be positive")
    if type(limit) is not int or limit < 1:
        raise ValueError("candidate limit must be a positive integer")
    before = file_state(source)
    if index is None:
        index = catalog(source)
    else:
        if not isinstance(index, dict) or index.get("sha256") != sha256(source):
            raise ValueError("saved catalog does not match source bytes")
        rows = index.get("frames")
        if not isinstance(rows, list) or not rows:
            raise ValueError("saved catalog needs frame timestamps")
        previous = -math.inf
        for i, row in enumerate(rows):
            if (not isinstance(row, dict) or type(row.get("frame")) is not int or row["frame"] != i
                    or type(row.get("pts")) not in (int, float) or not math.isfinite(row["pts"])
                    or row["pts"] < previous):
                raise ValueError("saved catalog has invalid frame timestamps")
            previous = row["pts"]
    if file_state(source) != before:
        raise ValueError("source changed while loading catalog")
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
    detector = cv2.SIFT_create(nfeatures=1000)
    query_features = detector.detectAndCompute(cv2.cvtColor(np.asarray(reference), cv2.COLOR_RGB2GRAY), None)
    for frame, image in selected_frames(source, selected, 640, frame_count=len(index["frames"])):
        results.append(
            {
                "frame": frame,
                "pts": index["frames"][frame]["pts"],
                **correspondence(reference, image, detector=detector, query_features=query_features),
            }
        )
    if file_state(source) != before or sha256(source) != index["sha256"] or file_state(source) != before:
        raise ValueError("source changed during screenshot search")
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
    p.add_argument("--index", type=Path, help="Reuse an existing source_scan catalog after checking source bytes")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    if Path(a.out).resolve() in (Path(a.query).resolve(), Path(a.source).resolve()):
        p.error("output would overwrite input")
    if Path(a.out).exists() or Path(a.out).is_symlink():
        p.error("output already exists choose a new report path")
    save_json(a.out, search(a.query, a.source, a.every, index=load_json(a.index) if a.index else None), exclusive=True)


if __name__ == "__main__":
    main()
