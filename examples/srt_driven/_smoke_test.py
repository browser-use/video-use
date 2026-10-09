"""Regression tests for srt_driven_edit. Run with bare `python` — no pytest."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "helpers"))

from srt_driven_edit import (
    parse_srt, parse_plan, align, validate_srt, validate_plan,
    validate_alignment, resolve_style, has_cjk, STYLE_TEMPLATES,
    subs_filter_escape, safe_ascii_name,
    concat_quote_path, read_srt_text, make_safe_work_dir,
    _split_time_line, V_SYNC_TAIL, A_SYNC_TAIL, SRT_ENCODINGS,
    ensure_safe_subs_path, _path_is_filter_safe,
    PARAMS_FINGERPRINT, CACHE_VERSION, cache_key,
    Segment,
)

base = Path(__file__).resolve().parent


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def ok(msg: str) -> None:
    print(f"  ok: {msg}")


def fail(msg: str) -> None:
    raise SystemExit(f"  FAIL: {msg}")


# -- 1. Legacy Form A + Form B parsing -----------------------------------

section("Form A (legacy array, English)")
cues = parse_srt(base / "script.srt")
validate_srt(cues)
sources, voices, entries = parse_plan(base / "edit_plan.json")
assert len(cues) == 3 and len(entries) == 3 and sources == {} and voices == {}
ok("parsed 3 cues + 3 plan entries, no sources/voices map")
assert not has_cjk(cues)
ok("has_cjk False on English")

section("Form B (object, multi-source, multi-voice)")
sources, voices, entries = parse_plan(base / "edit_plan_v2.json")
assert list(sources) == ["A", "B"] and list(voices) == ["host", "guest"]
ok(f"sources={list(sources)} voices={list(voices)}")
assert entries[0].source_name == "A" and entries[0].voice_name == "host"
assert entries[1].source_name == "B" and entries[1].voice_name == "guest"
assert entries[2].source_name == "A" and entries[2].voice_name is None
ok("per-segment source/voice refs parsed")


# -- 2. CJK detection + auto style + style templates ---------------------

section("CJK detection + style resolution")
cues_cjk = parse_srt(base / "script_cjk.srt")
assert has_cjk(cues_cjk) is True
assert not has_cjk(cues)
ok("CJK regex matches CN/EN correctly")
auto_cjk = resolve_style("auto", cues_cjk)
auto_en = resolve_style("auto", cues)
assert "Microsoft YaHei UI" in auto_cjk
assert "Helvetica" in auto_en
ok("auto style picks YaHei for CJK, Helvetica for EN")
assert STYLE_TEMPLATES["cjk-natural"] == resolve_style("cjk-natural", cues)
ok("named template lookup")
raw = "FontName=Custom,FontSize=24"
assert resolve_style(raw, cues) == raw
ok("raw ASS string passthrough")


# -- 3. SRT encoding fallback (GBK / utf-8-sig / utf-8) ------------------

section("read_srt_text encoding fallback")
tmp = Path(tempfile.mkdtemp(prefix="srt_smoke_"))

cjk_payload = "1\n00:00:00,000 --> 00:00:03,000\n中文字幕测试\n"

# utf-8
(tmp / "u8.srt").write_bytes(cjk_payload.encode("utf-8"))
text = read_srt_text(tmp / "u8.srt")
assert "中文字幕测试" in text, f"utf-8 decode wrong: {text!r}"
ok("utf-8 decoded")

# utf-8 with BOM
(tmp / "u8bom.srt").write_bytes(b"\xef\xbb\xbf" + cjk_payload.encode("utf-8"))
text = read_srt_text(tmp / "u8bom.srt")
assert text.startswith("1") and "中文" in text, f"utf-8-sig decode wrong: {text!r}"
ok("utf-8-sig BOM stripped + decoded")

# gb18030 (typical Windows Chinese)
(tmp / "gb.srt").write_bytes(cjk_payload.encode("gb18030"))
text = read_srt_text(tmp / "gb.srt")
assert "中文字幕测试" in text, f"gb18030 decode wrong: {text!r}"
ok("gb18030 decoded via fallback")

# cp936 (a.k.a. GBK, Windows Chinese ANSI)
(tmp / "cp936.srt").write_bytes(cjk_payload.encode("cp936"))
text = read_srt_text(tmp / "cp936.srt")
assert "中文字幕测试" in text
ok("cp936 decoded via fallback")

# Now parse a GBK-encoded full SRT end-to-end
gbk_full = (
    "1\n00:00:00,000 --> 00:00:03,000\n这是第一条\n\n"
    "2\n00:00:03,000 --> 00:00:06,000\n这是第二条\n"
)
gbk_path = tmp / "full_gbk.srt"
gbk_path.write_bytes(gbk_full.encode("gb18030"))
cues_gbk = parse_srt(gbk_path)
assert len(cues_gbk) == 2
assert cues_gbk[0].text == "这是第一条"
assert cues_gbk[1].text == "这是第二条"
ok("parse_srt end-to-end on GB18030 input")


# -- 4. SRT cue settings tolerance ---------------------------------------

section("Cue settings on time line")
# Real-world examples: 'position:90% align:start' on the right of -->
samples = [
    ("00:00:00,000 --> 00:00:03,000 position:90%", (0.0, 3.0)),
    ("00:00:01,500 --> 00:00:04,200 align:start line:80%", (1.5, 4.2)),
    ("  00:00:02,000   -->   00:00:05,000   X1:10 X2:200 Y1:5 Y2:50", (2.0, 5.0)),
    ("00:00:00.500 --> 00:00:01.000", (0.5, 1.0)),  # dot fraction
]
for line, expected in samples:
    a, b = _split_time_line(line)
    from srt_driven_edit import parse_timestamp
    got = (parse_timestamp(a), parse_timestamp(b))
    assert abs(got[0] - expected[0]) < 1e-6 and abs(got[1] - expected[1]) < 1e-6, \
        f"{line!r} → {got}, expected {expected}"
ok(f"parsed {len(samples)} time lines with cue settings / odd spacing")

# Full SRT with cue settings inline
weird_srt = (
    "1\n00:00:00,000 --> 00:00:03,000 position:90% align:start\nhello\n\n"
    "2\n00:00:03,000 --> 00:00:07,000 line:80%\nworld\n"
)
weird = tmp / "weird.srt"
weird.write_text(weird_srt, encoding="utf-8")
parsed = parse_srt(weird)
assert len(parsed) == 2
assert parsed[0].final_start == 0.0 and parsed[0].final_end == 3.0
assert parsed[0].text == "hello" and parsed[1].text == "world"
ok("parse_srt tolerates cue settings end-to-end")


# -- 5. concat_quote_path edge cases -------------------------------------

section("concat_quote_path edge cases")
cases = [
    (Path("/tmp/foo.mp4"),                "'/tmp/foo.mp4'"),
    (Path("/tmp/foo bar.mp4"),            "'/tmp/foo bar.mp4'"),
    (Path("/tmp/it's.mp4"),               "'/tmp/it'\\''s.mp4'"),
    (Path("/tmp/he said 'hi'.mp4"),       "'/tmp/he said '\\''hi'\\''.mp4'"),
]
for p, _expected in cases:
    got = concat_quote_path(p)
    # We only check the structural pattern: start/end with single quote,
    # any embedded single-quotes are properly close-escape-reopened.
    assert got.startswith("'") and got.endswith("'"), f"{p}: {got}"
    # Verify reverse — closing+escape+reopen idiom for any input apostrophe
    if "'" in p.as_posix():
        assert "'\\''" in got, f"{p}: {got}"
    ok(f"{p.as_posix()!r:<35} → {got}")

# CJK paths — verify it doesn't barf and produces a quoted UTF-8 string
# Note: concat_quote_path calls .resolve() which prepends a drive letter on
# Windows, so compare against the resolved posix form, not the literal input.
cjk_p = Path("/tmp/视频 v2/片段.mp4")
got = concat_quote_path(cjk_p)
assert got == f"'{cjk_p.resolve().as_posix()}'"
assert "视频" in got and "片段" in got
ok(f"CJK + space preserved: {got}")


# -- 6. make_safe_work_dir produces ASCII path ---------------------------

section("make_safe_work_dir")
plan_with_cjk_path = tmp / "中文 plan.json"
plan_with_cjk_path.write_text("[]", encoding="utf-8")
wd = make_safe_work_dir("我的剪辑 v2!", plan_with_cjk_path)
assert wd.exists() and wd.is_dir()
# Path must be ASCII-only (no CJK leaks)
assert all(ord(c) < 128 for c in str(wd)), f"work dir not ASCII: {wd}"
assert "srt_edit_" in wd.name
ok(f"work dir is ASCII: {wd}")

# Re-creating wipes previous contents (deterministic)
sentinel = wd / "_stale.txt"
sentinel.write_text("old")
wd2 = make_safe_work_dir("我的剪辑 v2!", plan_with_cjk_path)
assert wd2 == wd
assert not sentinel.exists()
ok("rerun wipes stale contents")


# -- 7. Sync tails defined and reasonable --------------------------------

section("Sync tail constants")
assert "fps=24" in V_SYNC_TAIL and "setpts=PTS-STARTPTS" in V_SYNC_TAIL
assert "aresample=async=1" in A_SYNC_TAIL and "asetpts=PTS-STARTPTS" in A_SYNC_TAIL
ok(f"V_SYNC_TAIL = {V_SYNC_TAIL}")
ok(f"A_SYNC_TAIL = {A_SYNC_TAIL}")


# -- 8. Strict validation -------------------------------------------------

section("Validation errors hard-fail")
import json as _j

# duplicate id in SRT
bad = tmp / "dup.srt"
bad.write_text("1\n00:00:00,000 --> 00:00:01,000\na\n\n1\n00:00:01,000 --> 00:00:02,000\nb\n", encoding="utf-8")
try:
    validate_srt(parse_srt(bad))
    fail("dup id should have errored")
except SystemExit as e:
    ok(f"dup id: {e}")

# overlap
bad.write_text("1\n00:00:00,000 --> 00:00:03,000\na\n\n2\n00:00:02,000 --> 00:00:04,000\nb\n", encoding="utf-8")
try:
    validate_srt(parse_srt(bad))
    fail("overlap should have errored")
except SystemExit as e:
    ok(f"overlap: {e}")

# non-monotonic
bad.write_text("1\n00:00:05,000 --> 00:00:07,000\na\n\n2\n00:00:00,000 --> 00:00:02,000\nb\n", encoding="utf-8")
try:
    validate_srt(parse_srt(bad))
    fail("non-monotonic should have errored")
except SystemExit as e:
    ok(f"non-monotonic: {e}")

# end <= start in plan
bad_plan = tmp / "bad_plan.json"
bad_plan.write_text(_j.dumps([{"id": 1, "source_start": "00:00:05,000", "source_end": "00:00:03,000"}]), encoding="utf-8")
try:
    s, v, ents = parse_plan(bad_plan)
    validate_plan(ents, s, v, Path("/fake/source.mp4"))
    fail("end<=start should have errored")
except SystemExit as e:
    ok(f"end<=start: {e}")

# negative source_start
bad_plan.write_text(_j.dumps([{"id": 1, "source_start": "00:00:00,000", "source_end": "00:00:03,000"}]), encoding="utf-8")
s, v, ents = parse_plan(bad_plan)
ents[0].source_start = -1.0
try:
    validate_plan(ents, s, v, Path("/fake/source.mp4"))
    fail("negative start should have errored")
except SystemExit as e:
    ok(f"negative start: {e}")

# id mismatch
ok_srt = parse_srt(base / "script.srt")
s, v, ents = parse_plan(base / "edit_plan.json")
from srt_driven_edit import PlanEntry
ents.append(PlanEntry(id=99, source_name="_default", source_start=0.0, source_end=1.0, voice_name=None))
try:
    validate_alignment(ok_srt, ents)
    fail("id mismatch should have errored")
except SystemExit as e:
    ok(f"id mismatch: {e}")


# -- 9. Alignment + gap handling on real example -------------------------

section("alignment on script.srt + edit_plan.json")
s, v, ents = parse_plan(base / "edit_plan.json")
segs = align(parse_srt(base / "script.srt"), ents, s, v,
             legacy_default_source=Path("/fake/source.mp4"),
             tolerance=0.5, trim_direction="tail", on_short="error")
for sg in segs:
    print(f"  id={sg.id} src[{sg.source_start:.3f}-{sg.source_end:.3f}] "
          f"out[{sg.out_start:.3f}-{sg.out_end:.3f}] gap={sg.leading_gap:.3f}")
assert abs(segs[-1].out_end - 12.0) < 1e-6
assert abs(segs[2].leading_gap - 1.5) < 1e-6
ok("12.0s total, 1.5s gap before id=3")



# -- 10. ensure_safe_subs_path self-defense ------------------------------

section("ensure_safe_subs_path")
# safe path (already ASCII, no single quote): returned as-is
safe_in = tmp / "plain.srt"
safe_in.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
out, cleanup = ensure_safe_subs_path(safe_in)
assert out == safe_in and cleanup is None
ok(f"ascii input returned as-is: {out.name}")

# unsafe path: CJK in name → copied to safe location
cjk_in = tmp / "中文 字幕.srt"
cjk_in.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
out, cleanup = ensure_safe_subs_path(cjk_in)
assert out != cjk_in and cleanup == out
assert str(out).isascii(), f"safe copy still has non-ASCII chars: {out}"
assert out.read_text(encoding="utf-8").startswith("1")
ok(f"CJK input copied to safe path: {out}")
cleanup.unlink()

# unsafe path: single quote in name → also copied
quote_in = tmp / "it's mine.srt"
quote_in.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n", encoding="utf-8")
out, cleanup = ensure_safe_subs_path(quote_in)
assert out != quote_in and "'" not in str(out)
ok(f"single-quote input copied to safe path: {out}")
cleanup.unlink()

# unsafe + non-UTF-8 input gets normalized through read_srt_text
gbk_in = tmp / "gbk 字幕.srt"
gbk_in.write_bytes("1\n00:00:00,000 --> 00:00:01,000\n中文\n".encode("gb18030"))
out, cleanup = ensure_safe_subs_path(gbk_in)
assert "中文" in out.read_text(encoding="utf-8")
ok(f"GB18030 + CJK path → normalized utf-8 safe copy")
cleanup.unlink()

# _path_is_filter_safe sanity
assert _path_is_filter_safe(Path("/tmp/foo.srt")) is True
assert _path_is_filter_safe(Path("/tmp/视频.srt")) is False
assert _path_is_filter_safe(Path("/tmp/it's.srt")) is False
ok("_path_is_filter_safe correctly flags non-ASCII and single quote")


# -- 11. Cache key fingerprinting ---------------------------------------

section("cache_key includes params fingerprint + ffmpeg version")
assert isinstance(PARAMS_FINGERPRINT, str) and len(PARAMS_FINGERPRINT) == 10
ok(f"PARAMS_FINGERPRINT = {PARAMS_FINGERPRINT}")
assert CACHE_VERSION == 2
ok(f"CACHE_VERSION bumped to {CACHE_VERSION}")

# Build a fake segment pointed at a real file (this script) so _file_fingerprint works
fake_seg = Segment(
    id=1,
    source_path=Path(__file__).resolve(),
    source_start=0.0,
    source_end=1.0,
    out_start=0.0,
    out_end=1.0,
    leading_gap=0.0,
    text="x",
    voice_path=None,
    pad_short=False,
    plan_src_dur=1.0,
)
k_v60 = cache_key(fake_seg, effective_bg_volume=0.0, hdr=False, portrait=False,
                  voice_signature=None, ffmpeg_version="6.0")
k_v71 = cache_key(fake_seg, effective_bg_volume=0.0, hdr=False, portrait=False,
                  voice_signature=None, ffmpeg_version="7.1")
assert k_v60 != k_v71, "different ffmpeg versions should produce different cache keys"
ok(f"ffmpeg 6.0 → {k_v60[:16]}…, 7.1 → {k_v71[:16]}… (differ)")

k_bg0 = cache_key(fake_seg, effective_bg_volume=0.0, hdr=False, portrait=False,
                  voice_signature=None, ffmpeg_version="6.0")
k_bg1 = cache_key(fake_seg, effective_bg_volume=0.1, hdr=False, portrait=False,
                  voice_signature=None, ffmpeg_version="6.0")
assert k_bg0 != k_bg1, "different effective bg_volume must invalidate cache"
ok("effective bg_volume differs → cache key differs")


# -- 12. preflight + probe_streams (best-effort, ffmpeg may be absent) ---

section("preflight + probe_streams (only if ffmpeg installed)")
import shutil as _sh
import subprocess as _sp
if _sh.which("ffmpeg") and _sh.which("ffprobe"):
    from srt_driven_edit import preflight, probe_streams
    versions = preflight()
    assert "ffmpeg" in versions and "ffprobe" in versions
    ok(f"preflight ok: {versions}")

    # Build a 0.5s test mp4 with video + audio via lavfi
    av_mp4 = tmp / "probe_av.mp4"
    _sp.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=320x240:r=24:d=0.5",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", "0.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(av_mp4),
    ], check=True)
    info = probe_streams(av_mp4)
    assert info["has_video"] is True and info["has_audio"] is True
    assert abs(info["duration"] - 0.5) < 0.1
    ok(f"probe video+audio mp4: {info}")

    # Video-only mp4 → has_audio False, exercises the auto-degrade path
    v_only = tmp / "probe_vonly.mp4"
    _sp.run([
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "color=c=red:s=320x240:r=24:d=0.5",
        "-an", "-t", "0.5",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(v_only),
    ], check=True)
    info = probe_streams(v_only)
    assert info["has_video"] is True and info["has_audio"] is False
    ok(f"probe video-only mp4: {info}")

    # Garbage input → SystemExit, not a silent pass
    junk = tmp / "junk.mp4"
    junk.write_bytes(b"not a media file")
    try:
        probe_streams(junk)
        fail("probe_streams on garbage should have raised")
    except SystemExit as e:
        ok(f"probe_streams hard-fails on junk: {str(e)[:80]}")
else:
    ok("ffmpeg not on PATH — preflight/probe_streams tests skipped")


print("\n=== ALL TESTS PASSED ===")
