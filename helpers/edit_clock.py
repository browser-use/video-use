"""One rational frame clock and one 48 kHz sample clock. Intervals are half-open."""

from fractions import Fraction
import math

SAMPLE_RATE = 48000


# require a finite positive clock rate before conversion
def clock_rate(value):
    if isinstance(value, bool):
        raise ValueError("clock rate must be finite and positive")
    try:
        result = fraction(value)
    except (ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ValueError("clock rate must be finite and positive") from exc
    if result <= 0:
        raise ValueError("clock rate must be finite and positive")
    return result


# convert decimal or rational input without introducing binary float rounding
def fraction(value):
    """Convert decimal or rational input without introducing binary float rounding."""
    return value if isinstance(value, Fraction) else Fraction(str(value))


# round a nonnegative rational value halfway upward
def nearest(value):
    """Round nonnegative rational values half up; avoid Python's banker rounding."""
    value = fraction(value)
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


# convert seconds to a frame boundary using the requested rounding policy
def seconds_to_frame(seconds, fps=30, mode="nearest"):
    """Convert seconds to a frame boundary using the requested rounding policy."""
    value = fraction(seconds) * clock_rate(fps)
    if mode == "nearest":
        return nearest(value)
    if mode == "ceil":
        return math.ceil(value)
    if mode == "floor":
        return math.floor(value)
    raise ValueError("mode must be nearest, ceil, or floor")


# map a frame boundary onto the audio sample clock
def frame_to_sample(frame, fps=30, rate=SAMPLE_RATE):
    """Map a frame boundary onto the audio sample clock."""
    return nearest(fraction(frame) * clock_rate(rate) / clock_rate(fps))


# map seconds onto the audio sample clock
def seconds_to_sample(seconds, rate=SAMPLE_RATE):
    """Map seconds onto the audio sample clock."""
    return nearest(fraction(seconds) * clock_rate(rate))


# floor a frame boundary to the centisecond clock used by ASS subtitles
def ass_stamp(frame, fps=30):
    """Floor to the ASS clock so a 30 fps end boundary cannot leak one frame."""
    cs = math.floor(fraction(frame) * 100 / clock_rate(fps))
    return f"{cs//360000}:{cs//6000%60:02}:{cs//100%60:02}.{cs%100:02}"


# distribute a fixed frame budget across positive shot weights
def allocate_frames(total, weights):
    """Largest-remainder allocation preserves total; it does not choose edit points."""
    if type(total) is not int or not weights or total < len(weights) or any(fraction(w) <= 0 for w in weights):
        raise ValueError("positive weights and at least one frame per shot required")
    exact = [fraction(w) * total / sum(map(fraction, weights)) for w in weights]
    counts = [math.floor(v) for v in exact]
    for i in sorted(
        range(len(weights)), key=lambda i: (exact[i] - counts[i], -i), reverse=True
    )[: total - sum(counts)]:
        counts[i] += 1
    if min(counts) < 1:
        extra = total - len(weights)
        exact = [fraction(w) * extra / sum(map(fraction, weights)) for w in weights]
        counts = [1 + math.floor(v) for v in exact]
        for i in sorted(range(len(weights)), key=lambda i: (exact[i] % 1, -i), reverse=True)[:total - sum(counts)]:
            counts[i] += 1
    return counts


# reject shot intervals with gaps overlaps or an incorrect total
def check_partition(shots, total):
    """Reject shot intervals with gaps overlaps or an incorrect total."""
    cursor = 0
    for shot in shots:
        start, end = shot["start_frame"], shot["end_frame"]
        if (
            type(start) is not int
            or type(end) is not int
            or start != cursor
            or end <= start
        ):
            raise ValueError(
                f'non-contiguous or invalid shot: {shot.get("id", "unknown")}'
            )
        cursor = end
    if cursor != total:
        raise ValueError(f"shots cover {cursor} frames, expected {total}")
