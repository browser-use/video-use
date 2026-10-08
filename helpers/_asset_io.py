"""Shared staging and bounded downloads for still asset helpers."""

import argparse
import functools
import hashlib
import json
import tempfile
import os
import io
import contextlib
from pathlib import Path


# hash an asset without loading the complete file into memory
def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# stage each asset and provenance file before publishing to new destinations
def staged_asset(function):
    # preserve the public namespace interface while isolating generated files
    @functools.wraps(function)
    def run(args):
        output = Path(args.output).absolute()
        destinations = [output, output.with_suffix(output.suffix + ".json")]
        if function.__name__ == "fetch_logo" and output.suffix.lower() == ".png":
            destinations.append(output.with_suffix(".svg"))
        for path in destinations:
            if path.exists() or path.is_symlink():
                raise FileExistsError(f"output already exists use a new name: {path}")
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix="asset-", dir=output.parent
        ) as directory:
            staged = Path(directory) / output.name
            copied = argparse.Namespace(**vars(args))
            copied.output = staged
            with contextlib.redirect_stdout(io.StringIO()):
                function(copied)
            metadata_path = staged.with_suffix(staged.suffix + ".json")
            metadata = json.loads(metadata_path.read_text())
            metadata["sha256"] = file_hash(staged)
            metadata["rights_verified"] = False
            metadata_path.write_text(
                json.dumps(metadata, indent=1) + "\n", encoding="utf-8"
            )
            # exclusive creates also protect a destination created during generation
            created = []
            try:
                for destination in destinations:
                    source = Path(directory) / destination.name
                    os.link(source, destination)
                    created.append((source, destination))
            except BaseException:
                for source, destination in created:
                    if destination.exists() and not destination.is_symlink() and destination.samefile(source):
                        destination.unlink()
                raise
        print(f"saved {output}")

    return run


# stop a streaming download as soon as its declared or actual body exceeds the limit
def download(url, *, headers, max_bytes):
    import requests

    try:
        with requests.get(url, headers=headers, timeout=60, stream=True) as response:
            if response.status_code != 200:
                raise ValueError(f"download failed with HTTP {response.status_code}")
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("download exceeds the byte limit")
            data = bytearray()
            for chunk in response.iter_content(chunk_size=65536):
                if len(data) + len(chunk) > max_bytes:
                    raise ValueError("download exceeds the byte limit")
                data.extend(chunk)
            return bytes(data), response.headers.get("Content-Type", ""), response.url
    except requests.RequestException as exc:
        raise ValueError(f"asset download failed: {exc}") from exc
