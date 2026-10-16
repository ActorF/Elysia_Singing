"""Archive matching upstream FFmpeg sources, licenses and native build provenance.

The binary version strings identify two FFmpeg revisions. Official GitHub source
snapshots are accompanied by their COPYING files and each local executable's
version, configuration and license output. This sidecar preserves known upstream
materials; it does not claim to reconstruct the vendors' statically linked third
party dependencies, build scripts or any unreported vendor patches.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import uuid
import zipfile


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_BINARIES = _PROJECT_ROOT / "models" / "cache" / "GPT-SoVITS-v2-240821"
_REVISIONS = {"ffmpeg": "fbb9368226", "ffprobe": "607ecc27ed"}
_PROVENANCE = """FFmpeg source and build provenance for Elysia_Singing

The sources/ directory contains official FFmpeg/FFmpeg GitHub snapshots of the
commits reported in the included Windows executables' version strings. Full
commit IDs, download URLs, archive hashes and binary hashes are in metadata.json.
License texts copied from each snapshot are also in licenses/. The complete
upstream snapshots retain all original copyright and licensing notices.

build-info/ preserves each executable's actual -version, -buildconf and -L
output. ffmpeg.exe declares LGPL version 3 or later. ffprobe.exe declares GPL
version 3 or later and identifies the www.gyan.dev full build dated 2022-04-07.
The originating ffmpeg.exe build distributor is not identified by its version
string; its reported compiler, configuration and commit are retained verbatim.

The configuration output records enabled external libraries. Their exact vendor
revisions, static link objects, complete dependency source trees, original vendor
build scripts and any unreported vendor patches were not present in the supplied
runtime. These snapshots are therefore upstream FFmpeg source preservation, not
a verified complete vendor rebuild kit or a claim that every binary can be
reproduced byte-for-byte from this ZIP. No missing provenance is invented.

Upstream source: https://github.com/FFmpeg/FFmpeg
FFmpeg license information: https://ffmpeg.org/legal.html
Known ffprobe build provider: https://www.gyan.dev/ffmpeg/builds/

The Windows runtime bundle additionally retains Python's LICENSE.txt and the
included Python distributions' own licenses/notices. Those dependencies are
listed in windows-runtime.manifest.json alongside the runtime release asset.
"""


def _request(url: str):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "Elysia-Singing-release-builder"}),
        timeout=60,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while block := stream.read(8 * 1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _build_metadata(tool: str, short_revision: str, scratch: Path) -> tuple[dict[str, object], Path, dict[str, bytes]]:
    commit_url = f"https://api.github.com/repos/FFmpeg/FFmpeg/commits/{short_revision}"
    with _request(commit_url) as response:
        commit = json.load(response)
    revision = commit["sha"]
    if len(revision) != 40 or not revision.startswith(short_revision):
        raise RuntimeError("Official repository returned an unexpected source revision.")
    source_url = f"https://codeload.github.com/FFmpeg/FFmpeg/tar.gz/{revision}"
    snapshot = scratch / f"FFmpeg-{revision}.tar.gz"
    print(f"Downloading official FFmpeg sources for {tool}: {revision}", flush=True)
    with _request(source_url) as response, snapshot.open("xb") as target:
        shutil.copyfileobj(response, target)
    licenses: dict[str, bytes] = {}
    with tarfile.open(snapshot, "r:gz") as archive:
        for member in archive:
            parts = Path(member.name).parts
            if member.isfile() and len(parts) == 2 and parts[-1].startswith("COPYING"):
                source = archive.extractfile(member)
                if source is None:
                    raise RuntimeError("An upstream COPYING file could not be read.")
                with source:
                    licenses[parts[-1]] = source.read()
    if not {"COPYING.GPLv3", "COPYING.LGPLv3"}.issubset(licenses):
        raise RuntimeError("The source snapshot lacks required upstream GPL/LGPL notices.")
    executable = _BINARIES / f"{tool}.exe"
    reports = {}
    for label, flag in (("version", "-version"), ("buildconf", "-buildconf"), ("license", "-L")):
        report = subprocess.run(
            [str(executable), flag],
            cwd=_PROJECT_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
            text=True,
            timeout=30,
        ).stdout
        reports[label] = report
    if short_revision not in reports["version"]:
        raise RuntimeError(f"{tool}.exe no longer reports the expected source revision.")
    metadata = {
        "tool": tool,
        "binary": executable.name,
        "binarySha256": _sha256(executable),
        "upstreamCommit": revision,
        "commitUrl": f"https://github.com/FFmpeg/FFmpeg/commit/{revision}",
        "sourceUrl": source_url,
        "sourceArchive": snapshot.name,
        "sourceArchiveSha256": _sha256(snapshot),
        "sourceArchiveBytes": snapshot.stat().st_size,
        "completeVendorRebuildAvailable": False,
        "reports": reports,
    }
    return metadata, snapshot, licenses


def main() -> int:
    """Download the two official source snapshots and publish a validated sidecar ZIP."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_PROJECT_ROOT / "release-build" / "ffmpeg-corresponding-sources.zip")
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if output.exists():
        parser.error(f"Refusing to overwrite the existing sidecar: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        with tempfile.TemporaryDirectory(prefix="ffmpeg-sources-", dir=output.parent) as directory:
            scratch = Path(directory).resolve()
            if not scratch.is_relative_to(output.parent):
                raise RuntimeError("Source staging escaped the output directory.")
            records = []
            with zipfile.ZipFile(partial, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr("README.txt", _PROVENANCE)
                for tool, revision in _REVISIONS.items():
                    metadata, snapshot, licenses = _build_metadata(tool, revision, scratch)
                    archive.write(snapshot, f"sources/{snapshot.name}", compress_type=zipfile.ZIP_STORED)
                    for name, content in licenses.items():
                        archive.writestr(f"licenses/{tool}/{name}", content)
                    for label, report in metadata.pop("reports").items():
                        archive.writestr(f"build-info/{tool}-{label}.txt", report)
                    records.append(metadata)
                archive.writestr("metadata.json", json.dumps({"schemaVersion": 1, "binaries": records}, indent=2) + "\n")
            with zipfile.ZipFile(partial) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("The source sidecar failed ZIP integrity validation.")
            partial.rename(output)
            print(json.dumps({"archive": output.name, "bytes": output.stat().st_size, "sha256": _sha256(output)}, indent=2), flush=True)
    finally:
        if partial.exists():
            partial.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
