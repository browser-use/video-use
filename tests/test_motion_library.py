"""Catalog boundaries and pinned downloads, without third-party network access."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from helpers.motion_library import (
    LibraryError,
    MAX_ASSET_BYTES,
    _HTTPSRedirects,
    fetch_asset,
    load_entries,
    main,
    select_entries,
    validate_catalog,
)


# build a minimal reference entry with observed evidence
def record(identity="shape-continuity", kind="reference"):
    value = {"id": identity, "title": "Shape continuity", "kind": kind, "tags": ["morph", "identity"], "summary": "Preserve a shape through a continuous transformation.", "source_url": "https://example.org/study"}
    if kind == "reference":
        value.update(reuse="reference-only", evidence={"observed": "A persistent silhouette changes pose."})
    return value


# describe exact expected bytes for a fake HTTPS asset
def download(name="shape.svg", body=b"<svg/>"):
    return {"url": f"https://example.org/{name}", "filename": name, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}


# attach explicit redistribution permission to pinned fake downloads
def asset(*files):
    return {**record("licensed-shape", "asset"), "license": {"name": "CC0-1.0", "url": "https://example.org/license", "redistribution": True, "attribution": ""}, "downloads": list(files or [download()])}


# simulate a bounded HTTP response without network access
class Response(io.BytesIO):
    # store response bytes metadata and the number of bytes read
    def __init__(self, body, url="https://example.org/shape.svg", headers=None):
        super().__init__(body)
        self.url = url
        self.headers = headers or {}
        self.bytes_read = 0

    # expose the simulated final response address
    def geturl(self):
        return self.url

    # read simulated response bytes while recording consumption
    def read(self, size=-1):
        value = super().read(size)
        self.bytes_read += len(value)
        return value


# create a temporary catalog with caller supplied entries
def write_catalog(tmp_path, entries):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    path = library / "catalog.json"
    path.write_text(json.dumps({"version": 1, "entries": entries}))
    return path


# verify catalog relations and files survive relocation
def test_catalog_relations_and_files_survive_relocation(tmp_path):
    reference = record()
    recipe = {**record("curve-recipe", "recipe"), "related": [reference["id"]], "detail": "recipes/curve.md", "implementation": "recipes/curve.mjs"}
    path = write_catalog(tmp_path, [reference, recipe])
    (path.parent / "recipes").mkdir()
    (path.parent / recipe["detail"]).write_text("A focused recipe")
    (path.parent / recipe["implementation"]).write_text("throw Error('must never execute during discovery');")
    import shutil
    copied = tmp_path / "copied-framework" / "library"
    shutil.copytree(path.parent, copied)
    assert load_entries(copied / "catalog.json") == [reference, recipe]


# verify duplicate ids and unknown related ids are reported
def test_duplicate_ids_and_unknown_related_ids_are_reported(tmp_path):
    first = record()
    second = {**record(), "related": ["not-in-catalog"]}
    errors = validate_catalog({"version": 1, "entries": [first, second]}, tmp_path / "catalog.json")
    assert any("duplicate id" in error for error in errors)
    assert any("unknown related ID" in error for error in errors)


# verify recipe paths cannot escape the catalog directory
@pytest.mark.parametrize("field,value", [("detail", "../outside.md"), ("implementation", "/tmp/outside.mjs")])
def test_recipe_paths_cannot_escape_the_catalog_directory(tmp_path, field, value):
    entry = {**record("recipe", "recipe"), field: value}
    errors = validate_catalog({"version": 1, "entries": [entry]}, tmp_path / "library/catalog.json")
    assert any(field in error and ("escapes" in error or "relative path" in error) for error in errors)


# verify symlink detail escape is rejected
def test_symlink_detail_escape_is_rejected(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("outside")
    path = write_catalog(tmp_path, [{**record(), "detail": "escaped.md"}])
    (path.parent / "escaped.md").symlink_to(outside)
    with pytest.raises(LibraryError, match="escapes"):
        load_entries(path)


# verify search filters real metadata without loading detail
def test_search_filters_real_metadata_without_loading_detail(tmp_path):
    entries = [record(), {**record("camera-resource", "resource"), "title": "Camera arcs", "tags": ["camera"], "summary": "Camera motion around a hero."}]
    assert [e["id"] for e in select_entries(entries, "SHAPE transformation", kind="reference", tags=["MORPH"])] == ["shape-continuity"]
    assert select_entries(entries, "unrelated recipe") == []
    path = write_catalog(tmp_path, entries)
    assert main(["--catalog", str(path), "check"]) == 0


# verify cli json show and actionable missing id
def test_cli_json_show_and_actionable_missing_id(tmp_path, capsys):
    entry = record()
    path = write_catalog(tmp_path, [entry])
    assert main(["--catalog", str(path), "list", "shape", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["id"] == entry["id"]
    assert main(["--catalog", str(path), "show", entry["id"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["evidence"] == entry["evidence"]
    assert main(["--catalog", str(path), "show", "missing"]) == 1
    assert "use list or search" in capsys.readouterr().err


# verify reference and unlicensed fetch are denied without network or output
def test_reference_and_unlicensed_fetch_are_denied_without_network_or_output(tmp_path):
    calls = []
    opener = lambda url: calls.append(url)
    for entry in [record(), {**asset(), "license": {**asset()["license"], "redistribution": False}}]:
        with pytest.raises(LibraryError):
            fetch_asset(entry, tmp_path / "assets", opener=opener)
    assert calls == []
    assert not (tmp_path / "assets").exists()


# verify unpinned or unsafe downloads are rejected
@pytest.mark.parametrize("field,value", [("filename", "../escape.svg"), ("filename", "folder\\escape.svg"), ("sha256", "incorrect"), ("url", "http://example.org/shape.svg"), ("bytes", MAX_ASSET_BYTES + 1), ("bytes", True)])
def test_unpinned_or_unsafe_downloads_are_rejected(tmp_path, field, value):
    entry = asset()
    entry["downloads"][0][field] = value
    with pytest.raises(LibraryError, match="Invalid asset"):
        fetch_asset(entry, tmp_path / "assets", opener=lambda _: pytest.fail("network must not run"))


# verify verified download writes receipt and reuses valid cache
def test_verified_download_writes_receipt_and_reuses_valid_cache(tmp_path):
    entry = asset()
    calls = []
    # provide controlled download bytes and observable request behavior
    def opener(url):
        calls.append(url)
        return Response(b"<svg/>", headers={"Content-Length": "6"})
    result = fetch_asset(entry, tmp_path, opener=opener)
    assert (tmp_path / "shape.svg").read_bytes() == b"<svg/>"
    receipt = json.loads(Path(result["receipt"]).read_text())
    assert receipt["license"] == entry["license"]
    assert receipt["files"][0]["sha256"] == entry["downloads"][0]["sha256"]
    cached = fetch_asset(entry, tmp_path, opener=opener)
    assert cached["reused"] == ["shape.svg"]
    assert cached["downloaded"] == []
    assert len(calls) == 1
    assert not list(tmp_path.glob(".motion-*"))


# verify partial corrupt and oversize downloads publish nothing
@pytest.mark.parametrize("payload,error", [(b"<sv", "incomplete download"), (b"<SVG/>", "SHA256 mismatch"), (b"<svg/>extra", "exceeds pinned")])
def test_partial_corrupt_and_oversize_downloads_publish_nothing(tmp_path, payload, error):
    response = Response(payload)
    with pytest.raises(LibraryError, match=error):
        fetch_asset(asset(), tmp_path, opener=lambda _: response)
    assert list(tmp_path.iterdir()) == []
    assert response.bytes_read <= len(b"<svg/>") + 1


# verify second file failure does not publish the first
def test_second_file_failure_does_not_publish_the_first(tmp_path):
    entry = asset(download("first.svg", b"first"), download("second.svg", b"second"))
    responses = iter([Response(b"first"), Response(b"wrong!")])
    with pytest.raises(LibraryError, match="SHA256 mismatch"):
        fetch_asset(entry, tmp_path, opener=lambda _: next(responses))
    assert list(tmp_path.iterdir()) == []


# verify existing mismatched file is untouched and no download occurs
def test_existing_mismatched_file_is_untouched_and_no_download_occurs(tmp_path):
    target = tmp_path / "shape.svg"
    target.write_bytes(b"user edit")
    with pytest.raises(LibraryError, match="refusing overwrite"):
        fetch_asset(asset(), tmp_path, opener=lambda _: pytest.fail("network must not run"))
    assert target.read_bytes() == b"user edit"


# verify existing symlink is not used as a cache
def test_existing_symlink_is_not_used_as_a_cache(tmp_path):
    outside = tmp_path / "original.svg"
    outside.write_bytes(b"<svg/>")
    out = tmp_path / "assets"
    out.mkdir()
    (out / "shape.svg").symlink_to(outside)
    with pytest.raises(LibraryError, match="refusing overwrite"):
        fetch_asset(asset(), out, opener=lambda _: pytest.fail("network must not run"))
    assert outside.read_bytes() == b"<svg/>"


# verify file created during fetch is not overwritten
def test_file_created_during_fetch_is_not_overwritten(tmp_path):
    # provide controlled download bytes and observable request behavior
    def opener(_url):
        (tmp_path / "shape.svg").write_bytes(b"concurrent edit")
        return Response(b"<svg/>")
    with pytest.raises(LibraryError, match="appeared during fetch"):
        fetch_asset(asset(), tmp_path, opener=opener)
    assert (tmp_path / "shape.svg").read_bytes() == b"concurrent edit"
    assert not list(tmp_path.glob(".motion-*"))
    assert not list(tmp_path.glob("*.receipt.json"))


# verify https redirects cannot downgrade
def test_https_redirects_cannot_downgrade(tmp_path):
    with pytest.raises(LibraryError, match="non-HTTPS"):
        _HTTPSRedirects().redirect_request(None, None, 302, "Found", {}, "http://example.org/file")
    with pytest.raises(LibraryError, match="outside HTTPS"):
        fetch_asset(asset(), tmp_path, opener=lambda _: Response(b"<svg/>", url="http://example.org/file"))
    assert list(tmp_path.iterdir()) == []


# verify mismatched receipt is preserved before networking
def test_mismatched_receipt_is_preserved_before_networking(tmp_path):
    receipt = tmp_path / "motion-library-licensed-shape.receipt.json"
    receipt.write_text("user notes")
    with pytest.raises(LibraryError, match="Existing receipt differs"):
        fetch_asset(asset(), tmp_path, opener=lambda _: pytest.fail("network must not run"))
    assert receipt.read_text() == "user notes"


# verify claimed size mismatch is rejected before reading the response
def test_claimed_size_mismatch_is_rejected_before_reading_the_response(tmp_path):
    response = Response(b"<svg/>", headers={"Content-Length": "70000000"})
    with pytest.raises(LibraryError, match="Content-Length"):
        fetch_asset(asset(), tmp_path, opener=lambda _: response)
    assert response.bytes_read == 0
    assert list(tmp_path.iterdir()) == []


# verify combined asset limit is checked before networking
def test_combined_asset_limit_is_checked_before_networking(tmp_path):
    entry = asset(download("one.bin"), download("two.bin"))
    for item in entry["downloads"]:
        item["bytes"] = MAX_ASSET_BYTES // 2 + 1
    with pytest.raises(LibraryError, match="combined asset download"):
        fetch_asset(entry, tmp_path, opener=lambda _: pytest.fail("network must not run"))


# verify interrupted download leaves no partial file
def test_interrupted_download_leaves_no_partial_file(tmp_path):
    # simulate a connection failure during staged download
    class Interrupted(Response):
        # interrupt reading before a partial download can be published
        def read(self, _size=-1):
            raise KeyboardInterrupt
    with pytest.raises(KeyboardInterrupt):
        fetch_asset(asset(), tmp_path, opener=lambda _: Interrupted(b""))
    assert list(tmp_path.iterdir()) == []


# verify only original recipes with real local implementations can omit source
def test_only_original_recipes_with_real_local_implementations_can_omit_source(tmp_path):
    entry = {**record("original-morph", "recipe"), "authorship": "original", "implementation": "morph.mjs"}
    entry.pop("source_url")
    path = write_catalog(tmp_path, [entry])
    (path.parent / "morph.mjs").write_text("export const pose = time => time;")
    assert load_entries(path) == [entry]

    for changed in [{**entry, "authorship": "external"}, {key: value for key, value in entry.items() if key != "implementation"}, {**entry, "kind": "resource"}, {**entry, "source_url": "invalid"}]:
        errors = validate_catalog({"version": 1, "entries": [changed]}, path)
        assert any("source_url" in error for error in errors)
    errors = validate_catalog({"version": 1, "entries": [{**entry, "implementation": "missing.mjs"}]}, path)
    assert any("file does not exist" in error for error in errors)
