"""Split verified runtime archives and build a small self-bootstrapping release ZIP.

GitHub limits each release asset to less than 2 GiB. One-GiB parts can be
downloaded independently, checked, and resumed without putting binary models
in Git history. The manifest is included with the source ZIP so downloads are
bound to exact release bytes rather than a mutable latest-release lookup.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import zipfile


_ROOT = Path(__file__).resolve().parents[1]
_PART_BYTES = 1024 ** 3
_BLOCK_BYTES = 8 * 1024 ** 2


def split_archive(archive: Path, destination: Path) -> dict:
    """Write bounded numbered parts and return their lengths and SHA-256 digests."""
    complete_hash = hashlib.sha256()
    parts = []
    total = 0
    with archive.open("rb") as source:
        index = 1
        while block := source.read(_BLOCK_BYTES):
            name = f"{archive.name}.part{index:02d}"
            final = destination / name
            partial = final.with_suffix(final.suffix + ".tmp")
            part_hash = hashlib.sha256()
            size = 0
            with partial.open("wb") as target:
                while block:
                    target.write(block)
                    part_hash.update(block)
                    complete_hash.update(block)
                    size += len(block)
                    total += len(block)
                    if size >= _PART_BYTES:
                        break
                    block = source.read(min(_BLOCK_BYTES, _PART_BYTES - size))
            partial.replace(final)
            parts.append({"name": name, "size": size, "sha256": part_hash.hexdigest()})
            print(f"Prepared {name} ({size:,} bytes)", flush=True)
            index += 1
    if not total:
        raise ValueError("A runtime archive must not be empty.")
    return {"archive": archive.name, "size": total, "sha256": complete_hash.hexdigest(), "parts": parts}


def build_starter(root: Path, output: Path) -> None:
    """Package tracked maintained files plus the pinned runtime download manifest."""
    result = subprocess.run(["git", "ls-files", "-z"], cwd=root, check=True, capture_output=True)
    names = set(result.stdout.decode("utf-8").split("\0")) - {""}
    names.add("release-manifest.json")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in sorted(names):
            path = root / name
            if (not path.is_file() or path.is_symlink()
                    or Path(name).parts[0] in {"models", "workspace", "output", "input", "release-build"}):
                raise ValueError(f"Unexpected tracked release path: {name}")
            archive.write(path, "Elysia_Singing/" + name)


def main() -> int:
    """Create release parts, the immutable download manifest and starter ZIP."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", default="v0.1.0")
    parser.add_argument("--starter-only", action="store_true", help="Rebuild only the ZIP after source fixes.")
    args = parser.parse_args()
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", args.tag):
        parser.error("Use a stable semantic version tag such as v0.1.0.")
    destination = _ROOT / "release-build"
    destination.mkdir(exist_ok=True)
    if not args.starter_only:
        bundles = {name: split_archive(destination / f"{name}-runtime.tar.gz", destination)
                   for name in ("windows", "linux")}
        manifest = {"schema_version": 1, "repository": "ActorF/Elysia_Singing",
                    "release_tag": args.tag, "bundles": bundles}
        (_ROOT / "release-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    starter = destination / "Elysia-Singing-Windows.zip"
    build_starter(_ROOT, starter)
    print(f"Starter package: {starter} ({starter.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
