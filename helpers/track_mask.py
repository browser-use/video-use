"""Track a manually selected subject polygon; flag uncertain frames for correction."""

import argparse
from pathlib import Path
import numpy as np
from edit_io import load_json, save_json


# detect crossings between nonadjacent polygon edges
def self_intersects(polygon):
    points = np.asarray(polygon, float)

    # measure the orientation of three polygon vertices
    def cross(a, b, c):
        return float((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]))

    for i in range(len(points)):
        a, b = points[i], points[(i + 1) % len(points)]
        for j in range(i + 1, len(points)):
            if j in (i, (i + 1) % len(points)) or (j + 1) % len(points) == i:
                continue
            c, d = points[j], points[(j + 1) % len(points)]
            if (
                cross(a, b, c) * cross(a, b, d) < 0
                and cross(c, d, a) * cross(c, d, b) < 0
            ):
                return True
    return False


# constrain grabcut around an existing matte and retain supplied thin-object protection
def refine_mask(image, seed, protected=None, iterations=3):
    """Constrain GrabCut around an existing matte and retain supplied thin-object protection."""
    import cv2

    seed = np.asarray(seed, np.uint8)
    if (
        seed.shape != image.shape[:2]
        or not np.any(seed > 127)
        or not np.any(seed < 128)
    ):
        raise ValueError(
            "refinement needs foreground and background seeds matching the image"
        )
    inside = (seed > 127).astype(np.uint8)
    kernel = np.ones((5, 5), np.uint8)
    labels = np.where(inside, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)
    labels[cv2.erode(inside, kernel) > 0] = cv2.GC_FGD
    labels[cv2.dilate(inside, kernel) == 0] = cv2.GC_BGD
    if protected is not None:
        labels[np.asarray(protected) > 127] = cv2.GC_FGD
    cv2.grabCut(
        image,
        labels,
        None,
        np.zeros((1, 65), float),
        np.zeros((1, 65), float),
        iterations,
        cv2.GC_INIT_WITH_MASK,
    )
    return np.where((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD), 255, 0).astype(
        np.uint8
    )


# propagate a reviewed polygon and flag uncertain tracking spans
def track(frames, seed_frame, polygon, max_error=20, max_bad_fraction=0.25):
    import cv2

    cv2.setNumThreads(1)
    if not frames or type(seed_frame) is not int or not 0 <= seed_frame < len(frames):
        raise ValueError("invalid seed frame")
    shape = frames[0].shape
    if len(shape) != 2 or any(f.shape != shape or f.dtype != np.uint8 for f in frames):
        raise ValueError("frames must be equally sized grayscale uint8 images")
    points_array = np.asarray(polygon, dtype=float)
    if (
        points_array.ndim != 2
        or points_array.shape[1] != 2
        or len(points_array) < 3
        or not np.isfinite(points_array).all()
    ):
        raise ValueError("polygon needs at least three finite pixel coordinates")
    area = np.sum(points_array[:, 0] * np.roll(points_array[:, 1], -1) - points_array[:, 1] * np.roll(points_array[:, 0], -1))
    if abs(area) <= 1e-9:
        raise ValueError("seed polygon must enclose a nonzero area")
    h, w = shape
    if (
        np.any(points_array < 0)
        or np.any(points_array[:, 0] >= w)
        or np.any(points_array[:, 1] >= h)
        or self_intersects(points_array)
    ):
        raise ValueError("seed polygon must be inside the image and not cross itself")
    if (
        not np.isfinite(max_error)
        or max_error <= 0
        or not np.isfinite(max_bad_fraction)
        or not 0 <= max_bad_fraction <= 1
    ):
        raise ValueError("invalid tracking thresholds")
    seed = np.array(polygon, np.float32).reshape(-1, 1, 2)
    rows = {
        seed_frame: {
            "frame": seed_frame,
            "polygon": points_array.tolist(),
            "needs_review": False,
            "seed": True,
        }
    }
    for indices in (range(seed_frame + 1, len(frames)), range(seed_frame - 1, -1, -1)):
        previous = seed_frame
        points = seed.copy()
        uncertain = False
        for i in indices:
            moved, status, error = cv2.calcOpticalFlowPyrLK(
                frames[previous], frames[i], points, None, winSize=(23, 23), maxLevel=3
            )
            if moved is None:
                moved = points.copy()
                bad = np.ones(len(points), bool)
            else:
                back, back_status, _ = cv2.calcOpticalFlowPyrLK(
                    frames[i],
                    frames[previous],
                    moved,
                    None,
                    winSize=(23, 23),
                    maxLevel=3,
                )
                bad = (status[:, 0] == 0) | (error[:, 0] > max_error)
                if back is None:
                    bad[:] = True
                else:
                    bad |= (back_status[:, 0] == 0) | (
                        np.linalg.norm(back[:, 0] - points[:, 0], axis=1) > 1.5
                    )
            if bad.any():
                delta = moved - points
                good = ~bad
                moved[bad] = points[bad] + (
                    np.median(delta[good], axis=0) if good.any() else 0
                )
            h, w = frames[i].shape[:2]
            uncertain = (
                uncertain
                or float(bad.mean()) > max_bad_fraction
                or self_intersects(moved[:, 0])
                or bool(
                    np.any(moved[:, :, 0] < 0)
                    | np.any(moved[:, :, 0] >= w)
                    | np.any(moved[:, :, 1] < 0)
                    | np.any(moved[:, :, 1] >= h)
                )
            )
            rows[i] = {
                "frame": i,
                "polygon": moved[:, 0].round(3).tolist(),
                "bad_point_fraction": float(bad.mean()),
                "needs_review": uncertain,
            }
            previous = i
            points = moved
    return {
        "frames": [rows[i] for i in range(len(frames))],
        "method": "manual silhouette with forward/backward pyramidal optical flow; not automatic semantic segmentation",
        "review": "Inspect every frame, including thin limbs and props; correct/reseed every flagged span.",
    }


# read prepared frames and export a new tracking document
def main():
    import cv2

    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("prepared_picture")
    p.add_argument("seed_json")
    p.add_argument("--out", required=True)
    a = p.parse_args()
    output = Path(a.out)
    if output.exists() or output.is_symlink():
        raise FileExistsError("tracking output must be a new file")
    seed = load_json(a.seed_json)
    cap = cv2.VideoCapture(a.prepared_picture)
    if not np.isclose(cap.get(cv2.CAP_PROP_FPS), 30, rtol=0, atol=1e-6):
        cap.release()
        raise ValueError("prepared picture must use the composition clock of 30 fps")
    frames = []
    while True:
        ok, bgr = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    cap.release()
    result = track(frames, seed["frame"], seed["polygon"])
    save_json(a.out, result, exclusive=True)
    print(
        "Tracked",
        len(frames),
        "frames; review flags:",
        sum(r["needs_review"] for r in result["frames"]),
    )


if __name__ == "__main__":
    main()
