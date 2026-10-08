#!/usr/bin/env python3
"""Discover curated motion references and fetch explicitly redistributable assets.

Only the catalog is consulted: no crawling, code execution, or archive extraction.
Run ``python helpers/motion_library.py --help`` for the compact command interface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "skills/motion-design/library/catalog.json"
MAX_ASSET_BYTES = 64 * 1024 * 1024
KINDS = {"reference", "resource", "asset", "recipe"}
ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
FILENAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}\Z")
SHA_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


# report an invalid catalog or an unsafe asset delivery
class LibraryError(ValueError):
    """An actionable catalog or asset-delivery failure."""


# accept only nonempty text metadata
def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


# validate web addresses and reject credentials or malformed ports
def _web_url(value: Any, *, https: bool = False) -> bool:
    if not isinstance(value, str) or any(ord(character) <= 32 for character in value):
        return False
    try:
        parsed = urllib.parse.urlsplit(value)
        parsed.port  # Validate malformed/out-of-range ports before networking.
        return bool(parsed.hostname) and not parsed.username and not parsed.password and parsed.scheme in ({"https"} if https else {"http", "https"})
    except ValueError:
        return False


# resolve an existing catalog file without escaping its directory
def confined_path(root: Path, value: Any) -> Path:
    if not _text(value) or any(ord(character) < 32 for character in value) or Path(value).is_absolute() or "\\" in value:
        raise LibraryError("must be a relative path inside the catalog directory")
    try:
        root = root.resolve()
        candidate = (root / value).resolve()
    except (OSError, RuntimeError, ValueError) as error:
        raise LibraryError(f"cannot resolve local path: {error}") from error
    if not candidate.is_relative_to(root):
        raise LibraryError("path escapes the catalog directory")
    if not candidate.is_file():
        raise LibraryError(f"file does not exist: {value}")
    return candidate


# validate explicit licensing and bounded pinned asset downloads
def _asset_errors(entry: dict) -> list[str]:
    errors = []
    license_info = entry.get("license")
    if not isinstance(license_info, dict):
        errors.append("license must declare name, url, redistribution, and attribution")
    else:
        if not _text(license_info.get("name")) or not _web_url(license_info.get("url")):
            errors.append("license.name and a valid license.url are required")
        if not isinstance(license_info.get("redistribution"), bool):
            errors.append("license.redistribution must be explicitly true or false")
        if not isinstance(license_info.get("attribution"), str):
            errors.append("license.attribution must be a string (empty when none is required)")
    downloads = entry.get("downloads")
    if not isinstance(downloads, list) or not downloads:
        return errors + ["downloads must be a nonempty list of pinned files"]
    names, total = set(), 0
    for index, item in enumerate(downloads):
        prefix = f"downloads[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        name = item.get("filename")
        if not isinstance(name, str) or not FILENAME_PATTERN.fullmatch(name):
            errors.append(f"{prefix}.filename must be a plain filename without paths")
        elif name in names:
            errors.append(f"duplicate download filename: {name}")
        else:
            names.add(name)
        if not _web_url(item.get("url"), https=True):
            errors.append(f"{prefix}.url must use HTTPS without embedded credentials")
        if not isinstance(item.get("sha256"), str) or not SHA_PATTERN.fullmatch(item["sha256"]):
            errors.append(f"{prefix}.sha256 must be 64 lowercase hexadecimal characters")
        size = item.get("bytes")
        if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
            errors.append(f"{prefix}.bytes must be a positive integer no larger than 64 MiB")
        else:
            total += size
    if total > MAX_ASSET_BYTES:
        errors.append("combined asset download exceeds the 64 MiB limit")
    if f"motion-library-{entry.get('id')}.receipt.json" in names:
        errors.append("download filename conflicts with the asset receipt")
    return errors


# check catalog structure local references and related entry identifiers
def validate_catalog(data: Any, catalog_path: Path) -> list[str]:
    if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
        return ["catalog must be an object with version: 1"]
    entries = data.get("entries")
    if not isinstance(entries, list):
        return ["catalog.entries must be a list"]
    errors, ids = [], set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"entries[{index}] must be an object")
            continue
        identity = entry.get("id")
        label = identity if isinstance(identity, str) else f"entries[{index}]"
        if not isinstance(identity, str) or not ID_PATTERN.fullmatch(identity):
            errors.append(f"{label}: id must contain lowercase letters, numbers, hyphens, or underscores")
        elif identity in ids:
            errors.append(f"{label}: duplicate id")
        else:
            ids.add(identity)
        for field in ("title", "summary"):
            if not _text(entry.get(field)):
                errors.append(f"{label}: {field} must be nonempty text")
        kind = entry.get("kind")
        if not isinstance(kind, str) or kind not in KINDS:
            errors.append(f"{label}: kind must be reference, resource, asset, or recipe")
        if not isinstance(entry.get("tags"), list) or not all(_text(tag) for tag in entry["tags"]):
            errors.append(f"{label}: tags must be a list of nonempty strings")
        original_implementation = kind == "recipe" and entry.get("authorship") == "original" and _text(entry.get("implementation"))
        if not (original_implementation and "source_url" not in entry) and not _web_url(entry.get("source_url")):
            errors.append(f"{label}: source_url must be an HTTP(S) URL; only an original recipe with a local implementation may omit it")
        for field in ("detail", "implementation"):
            if field in entry:
                if field == "implementation" and kind != "recipe":
                    errors.append(f"{label}: only recipes may declare an implementation")
                try:
                    confined_path(catalog_path.parent, entry[field])
                except LibraryError as error:
                    errors.append(f"{label}: {field} {error}")
        if kind == "reference":
            if entry.get("reuse") != "reference-only":
                errors.append(f"{label}: references require reuse: reference-only")
            evidence = entry.get("evidence")
            if not isinstance(evidence, (str, list, dict)) or not evidence:
                errors.append(f"{label}: references require an evidence record")
        if kind == "asset":
            errors.extend(f"{label}: {error}" for error in _asset_errors(entry))
        elif "downloads" in entry:
            errors.append(f"{label}: only asset entries may declare downloads")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        related = entry.get("related", [])
        if not isinstance(related, list) or not all(isinstance(item, str) for item in related):
            errors.append(f"{entry.get('id')}: related must be a list of entry IDs")
        else:
            for identity in related:
                if identity not in ids:
                    errors.append(f"{entry.get('id')}: unknown related ID {identity!r}")
    return errors


# load the catalog only after all entry contracts pass
def load_entries(catalog_path: Path = DEFAULT_CATALOG) -> list[dict]:
    try:
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise LibraryError(f"Cannot read catalog {catalog_path}: {error}") from error
    errors = validate_catalog(data, catalog_path)
    if errors:
        raise LibraryError("Catalog validation failed:\n- " + "\n- ".join(errors))
    return data["entries"]


# filter compact metadata without reading or executing recipe source
def select_entries(entries: list[dict], query: str = "", *, kind: str | None = None, tags: list[str] | None = None) -> list[dict]:
    words = query.casefold().split()
    wanted_tags = {tag.casefold() for tag in tags or []}
    selected = []
    for entry in entries:
        text = " ".join([entry["id"], entry["title"], entry["summary"], *entry["tags"]]).casefold()
        if kind and entry["kind"] != kind:
            continue
        if wanted_tags - {tag.casefold() for tag in entry["tags"]}:
            continue
        if all(word in text for word in words):
            selected.append(entry)
    return sorted(selected, key=lambda entry: entry["id"])


# keep asset redirects inside the encrypted transport contract
class _HTTPSRedirects(urllib.request.HTTPRedirectHandler):
    # reject redirects that downgrade transport or embed credentials
    def redirect_request(self, request, response, code, message, headers, new_url):
        if not _web_url(new_url, https=True):
            raise LibraryError(f"Refusing non-HTTPS asset redirect: {new_url}")
        return super().redirect_request(request, response, code, message, headers, new_url)


# open a bounded asset request without transparent content encoding
def _open_https(url: str):
    request = urllib.request.Request(url, headers={"User-Agent": "video-use-motion-library/1", "Accept-Encoding": "identity"})
    return urllib.request.build_opener(_HTTPSRedirects()).open(request, timeout=30)


# verify cached bytes against their pinned size and digest
def _file_matches(path: Path, item: dict) -> bool:
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item["bytes"]:
        return False
    with path.open("rb") as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest() == item["sha256"]


# stage a complete size checked and hash checked asset privately
def _stage_download(item: dict, destination: Path, opener: Callable) -> Path:
    fd, filename = tempfile.mkstemp(prefix=".motion-download-", dir=destination)
    temporary = Path(filename)
    try:
        with os.fdopen(fd, "wb") as output, opener(item["url"]) as response:
            if not _web_url(response.geturl(), https=True):
                raise LibraryError("asset response redirected outside HTTPS")
            content_length = response.headers.get("Content-Length")
            if content_length is not None and content_length != str(item["bytes"]):
                raise LibraryError(f"{item['filename']}: Content-Length does not match pinned bytes")
            digest, remaining = hashlib.sha256(), item["bytes"]
            while remaining:
                chunk = response.read(min(65536, remaining))
                if not chunk:
                    raise LibraryError(f"{item['filename']}: incomplete download ({remaining} bytes missing)")
                if len(chunk) > remaining:
                    raise LibraryError(f"{item['filename']}: response exceeds pinned byte count")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            if response.read(1):
                raise LibraryError(f"{item['filename']}: response exceeds pinned byte count")
            if digest.hexdigest() != item["sha256"]:
                raise LibraryError(f"{item['filename']}: SHA256 mismatch; no file published")
            output.flush()
            os.fsync(output.fileno())
        return temporary
    except BaseException as error:
        temporary.unlink(missing_ok=True)
        if isinstance(error, LibraryError) or not isinstance(error, Exception):
            raise
        raise LibraryError(f"{item['filename']}: download failed: {error}") from error


# publish staged bytes without replacing an existing destination
def _publish_without_overwrite(temporary: Path, target: Path) -> None:
    # Same-directory hard linking is atomic and fails if a concurrent file exists.
    # Unlike replace(), it can never overwrite a file created after our preflight.
    try:
        os.link(temporary, target)
    except FileExistsError as error:
        raise LibraryError(f"Destination appeared during fetch; refusing overwrite: {target}") from error
    temporary.unlink()


# fetch explicitly redistributable assets and preserve a verified receipt
def fetch_asset(entry: dict, output_dir: Path, *, opener: Callable = _open_https) -> dict:
    if entry.get("kind") != "asset":
        raise LibraryError(f"{entry.get('id')}: only asset entries can be fetched; references remain reference-only")
    if not isinstance(entry.get("id"), str) or not ID_PATTERN.fullmatch(entry["id"]):
        raise LibraryError("Asset id is not a safe catalog identifier")
    errors = _asset_errors(entry)
    if errors:
        raise LibraryError("Invalid asset: " + "; ".join(errors))
    if entry["license"]["redistribution"] is not True:
        raise LibraryError(f"{entry['id']}: license does not explicitly allow redistribution")
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    receipt = {"version": 1, "entry_id": entry["id"], "source_url": entry["source_url"], "license": entry["license"], "files": entry["downloads"]}
    receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
    receipt_path = output_dir / f"motion-library-{entry['id']}.receipt.json"
    receipt_matches = lambda: receipt_path.is_file() and not receipt_path.is_symlink() and receipt_path.stat().st_size == len(receipt_bytes) and receipt_path.read_bytes() == receipt_bytes
    if receipt_path.is_symlink() or (receipt_path.exists() and not receipt_matches()):
        raise LibraryError(f"Existing receipt differs; choose another output directory: {receipt_path}")
    missing, reused = [], []
    for item in entry["downloads"]:
        target = output_dir / item["filename"]
        if target.exists() or target.is_symlink():
            if not _file_matches(target, item):
                raise LibraryError(f"Existing file differs from the pinned asset; refusing overwrite: {target}")
            reused.append(item["filename"])
        else:
            missing.append(item)
    staged = []
    try:
        # Verify every missing file before publishing any of them.
        for item in missing:
            staged.append((_stage_download(item, output_dir, opener), output_dir / item["filename"]))
        for temporary, target in staged:
            _publish_without_overwrite(temporary, target)
        if receipt_path.exists() or receipt_path.is_symlink():
            if not receipt_matches():
                raise LibraryError(f"Receipt changed during fetch; refusing overwrite: {receipt_path}")
        else:
            fd, filename = tempfile.mkstemp(prefix=".motion-receipt-", dir=output_dir)
            temporary = Path(filename)
            staged.append((temporary, receipt_path))
            with os.fdopen(fd, "wb") as stream:
                stream.write(receipt_bytes)
            _publish_without_overwrite(temporary, receipt_path)
    except LibraryError:
        raise
    except OSError as error:
        raise LibraryError(f"Asset fetch failed: {error}") from error
    finally:
        for temporary, _target in staged:
            temporary.unlink(missing_ok=True)
    return {"id": entry["id"], "output_dir": str(output_dir), "downloaded": [item["filename"] for item in missing], "reused": reused, "receipt": str(receipt_path)}


# dispatch catalog discovery validation and explicit asset downloads
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("list", "search"):
        command = commands.add_parser(name)
        command.add_argument("query", nargs="?", default="")
        command.add_argument("--kind", choices=sorted(KINDS))
        command.add_argument("--tag", action="append", default=[])
        command.add_argument("--json", action="store_true")
    show = commands.add_parser("show")
    show.add_argument("id")
    show.add_argument("--json", action="store_true")
    commands.add_parser("check")
    fetch = commands.add_parser("fetch")
    fetch.add_argument("id")
    fetch.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        entries = load_entries(args.catalog)
        if args.command == "check":
            print(f"Catalog valid: {len(entries)} entries")
        elif args.command in {"list", "search"}:
            matches = select_entries(entries, args.query, kind=args.kind, tags=args.tag)
            if args.json:
                fields = ("id", "title", "kind", "tags", "summary", "source_url", "detail")
                print(json.dumps([{key: entry[key] for key in fields if key in entry} for entry in matches], indent=2))
            else:
                for entry in matches:
                    print(f"{entry['id']} [{entry['kind']}] {entry['title']}\n  {entry['summary']}")
                if not matches:
                    print("No matching entries")
        else:
            entry = next((entry for entry in entries if entry["id"] == args.id), None)
            if entry is None:
                raise LibraryError(f"Unknown entry {args.id!r}; use list or search to discover IDs")
            if args.command == "fetch":
                print(json.dumps(fetch_asset(entry, args.out), indent=2))
            elif args.json:
                print(json.dumps(entry, indent=2))
            else:
                print(json.dumps(entry, indent=2))
                if entry.get("detail"):
                    print("\n" + confined_path(args.catalog.parent, entry["detail"]).read_text(encoding="utf-8"))
        return 0
    except (LibraryError, OSError, UnicodeError) as error:
        print(f"motion-library: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
