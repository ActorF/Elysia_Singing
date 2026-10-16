"""Build and validate a portable Windows audio runtime from installed packages.

Only the installed dependency closure for Demucs, CUDA Torch, NumPy, SciPy and
SoundFile is included. Wheel RECORD inventories select package-owned files;
unrelated applications, local editable links, bytecode and installer metadata are
excluded. The resulting archive is extracted to an isolated directory and checked
before publication so a working source cache cannot conceal a missing dependency.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import uuid


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_CACHE = _PROJECT_ROOT / "models" / "cache"
_RUNTIME = _CACHE / "GPT-SoVITS-v2-240821" / "runtime"
_ROOT_PACKAGES = ("torch", "torchaudio", "numpy", "scipy", "soundfile", "demucs")
_EXCLUDED_NAMES = frozenset({"direct_url.json", "INSTALLER", "REQUESTED"})


def _read_dependencies() -> list[dict[str, object]]:
    # Resolve markers using the embedded Python's real Windows/Python 3.9
    # environment, not whichever interpreter happens to build the release.
    diagnostic = "\n".join(
        (
            "import importlib.metadata as metadata, json, pathlib, sys",
            "sys.path.append(sys.argv[1])",
            "from packaging.requirements import Requirement",
            "pending = [(name, '') for name in json.loads(sys.argv[2])]",
            "seen = set(); distributions = {}",
            "while pending:",
            "    name, extra = pending.pop()",
            "    key = (name.lower().replace('_', '-'), extra)",
            "    if key in seen: continue",
            "    seen.add(key)",
            "    distribution = metadata.distribution(name)",
            "    distributions[distribution.metadata['Name']] = distribution",
            "    for declaration in distribution.requires or []:",
            "        requirement = Requirement(declaration)",
            "        if requirement.marker and not requirement.marker.evaluate({'extra': extra}): continue",
            "        pending.append((requirement.name, ''))",
            "        pending.extend((requirement.name, item) for item in requirement.extras)",
            "result = []",
            "for name, distribution in sorted(distributions.items()):",
            "    result.append({'name': name, 'version': distribution.version,",
            "        'license': distribution.metadata.get('License', ''),",
            "        'homepage': distribution.metadata.get('Home-page', ''),",
            "        'files': [str(pathlib.Path(distribution.locate_file(item)).resolve()) for item in distribution.files or []]})",
            "print(json.dumps(result))",
        )
    )
    result = subprocess.run(
        [str(_RUNTIME / "python.exe"), "-B", "-c", diagnostic,
         str(_CACHE / "singing-runtime" / "site-packages"), json.dumps(_ROOT_PACKAGES)],
        cwd=_PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(result.stdout)


def _include_package_file(path: Path) -> bool:
    if not path.is_file() or path.is_symlink():
        return False
    if not path.is_relative_to(_CACHE):
        raise RuntimeError(f"A package RECORD points outside the cache: {path}")
    relative = path.relative_to(_CACHE)
    # RECORD can include generated entry-point executables outside site-packages.
    # The application directly imports these distributions and needs no launchers.
    if "site-packages" not in relative.parts:
        return False
    if "__pycache__" in relative.parts or path.suffix.lower() in {".pyc", ".pyo", ".egg-link", ".whl"}:
        return False
    if path.name in _EXCLUDED_NAMES:
        return False
    return True


def _collect_files(packages: list[dict[str, object]]) -> list[Path]:
    files: set[Path] = set()
    for package in packages:
        declared_files = package.pop("files")
        if not declared_files:
            raise RuntimeError(f"Package has no installed RECORD inventory: {package['name']}")
        files.update(Path(value) for value in declared_files if _include_package_file(Path(value)))
    # Retain the complete embedded interpreter's flat binary/stdlib distribution;
    # include/, Scripts/, Tcl, NLTK data and other application directories are not
    # part of this non-interactive audio worker. Python's license stays alongside it.
    for path in _RUNTIME.iterdir():
        if path.is_file() and not path.is_symlink() and path.suffix.lower() != ".pyc":
            files.add(path)
    for relative in (
        "GPT-SoVITS-v2-240821/ffmpeg.exe",
        "GPT-SoVITS-v2-240821/ffprobe.exe",
        "singing-runtime/torch/hub/checkpoints/955717e8-8726e21a.th",
    ):
        path = _CACHE / relative
        if not path.is_file():
            raise RuntimeError(f"Required native asset is missing: {path}")
        files.add(path)
    return sorted(files, key=lambda path: path.relative_to(_PROJECT_ROOT).as_posix())


def _write_archive(destination: Path, files: list[Path]) -> None:
    with destination.open("xb") as stream, gzip.GzipFile(
        filename="", mode="wb", fileobj=stream, compresslevel=6, mtime=0
    ) as compressed, tarfile.open(fileobj=compressed, mode="w|") as archive:
        for index, path in enumerate(files, start=1):
            if index == 1 or index % 1000 == 0:
                print(f"Archiving {index:,}/{len(files):,} files...", flush=True)
            relative = path.relative_to(_PROJECT_ROOT).as_posix()
            entry = archive.gettarinfo(str(path), arcname=relative)
            # Portable metadata contains no build-machine username or filesystem
            # timestamps and produces the same archive for the same selected files.
            entry.uid = entry.gid = 0
            entry.uname = entry.gname = ""
            entry.mtime = 0
            entry.mode = 0o644
            with path.open("rb") as source:
                archive.addfile(entry, source)


def _validate_archive(archive_path: Path, scratch: Path) -> None:
    print("Extracting the archive for isolated dependency validation...", flush=True)
    with tarfile.open(archive_path, mode="r:gz") as archive:
        # This archive is locally created, but explicit path checks keep the
        # validator safe if it is reused with a substituted release artifact.
        for member in archive:
            destination = (scratch / member.name).resolve()
            if not destination.is_relative_to(scratch) or not member.isfile():
                raise RuntimeError("Archive contains an unsafe path or non-file member.")
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise RuntimeError("Archive member could not be read.")
            with source, destination.open("xb") as target:
                shutil.copyfileobj(source, target)
    cache = scratch / "models" / "cache"
    python = cache / "GPT-SoVITS-v2-240821" / "runtime" / "python.exe"
    diagnostic = "\n".join(
        (
            "import os, pathlib, sys",
            "sys.path.insert(0, sys.argv[1])",
            "from scripts import song_cover_worker as audio",
            "cache = pathlib.Path(sys.argv[2]).resolve()",
            "site = cache / 'singing-runtime' / 'site-packages'",
            "audio._require_singing_runtime(site)",
            "audio._load_singing_runtime_modules(site)",
            "import numpy, torch, torchaudio, scipy, soundfile",
            "from scipy.signal import resample_poly",
            "from demucs.pretrained import get_model",
            "os.environ['TORCH_HOME'] = str(cache / 'singing-runtime' / 'torch')",
            "def _forbid_download(*args, **kwargs):",
            "    raise RuntimeError('Archive validation may not download missing assets.')",
            "torch.hub.download_url_to_file = _forbid_download",
            "model = get_model('htdemucs')",
            "assert model.models, 'Demucs model was not loaded'",
            "assert resample_poly(numpy.ones(16), 2, 1).shape == (32,)",
            "assert soundfile.available_formats(), 'SoundFile native library unavailable'",
            "outside = [value for value in sys.path[1:] if value and not pathlib.Path(value).resolve().is_relative_to(cache)]",
            "assert not outside, 'Runtime depends on external Python paths: ' + repr(outside)",
            "print('Isolated archive verified: strict Demucs identity, offline model load, audio imports and native resampling.')",
            "print('Torch=' + torch.__version__ + '; CUDA available=' + str(torch.cuda.is_available()))",
        )
    )
    subprocess.run(
        [str(python), "-B", "-c", diagnostic, str(_PROJECT_ROOT), str(cache)],
        cwd=scratch,
        check=True,
        timeout=180,
    )
    for executable in ("ffmpeg.exe", "ffprobe.exe"):
        subprocess.run(
            [str(cache / "GPT-SoVITS-v2-240821" / executable), "-version"],
            cwd=scratch,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=30,
        )


def main() -> int:
    """Build an authenticated release archive and adjacent package/size manifest."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_PROJECT_ROOT / "release-build" / "windows-runtime.tar.gz")
    arguments = parser.parse_args()
    output = arguments.output.resolve()
    if output.exists():
        parser.error(f"Refusing to overwrite an existing archive: {output}")
    manifest_path = output.with_name("windows-runtime.manifest.json")
    if manifest_path.exists():
        parser.error(f"Refusing to overwrite an existing manifest: {manifest_path}")
    output.parent.mkdir(parents=True, exist_ok=True)
    packages = _read_dependencies()
    files = _collect_files(packages)
    unpacked_bytes = sum(path.stat().st_size for path in files)
    print(f"Selected {len(packages)} packages, {len(files):,} files, {unpacked_bytes / 1024**3:.2f} GiB.", flush=True)
    temporary_archive = output.with_name(f".{output.name}.{uuid.uuid4().hex}.partial")
    try:
        _write_archive(temporary_archive, files)
        with tempfile.TemporaryDirectory(prefix="windows-runtime-validation-", dir=output.parent) as directory:
            scratch = Path(directory).resolve()
            _validate_archive(temporary_archive, scratch)
        digest = hashlib.sha256()
        with temporary_archive.open("rb") as archive:
            while block := archive.read(8 * 1024 * 1024):
                digest.update(block)
        manifest = {
            "schemaVersion": 1,
            "archive": output.name,
            "sha256": digest.hexdigest(),
            "archiveBytes": temporary_archive.stat().st_size,
            "unpackedBytes": unpacked_bytes,
            "fileCount": len(files),
            "packages": packages,
            "files": [path.relative_to(_PROJECT_ROOT).as_posix() for path in files],
        }
        # Windows rename fails when the destination exists, publishing the fully
        # validated archive atomically without replacing a concurrent build.
        temporary_archive.rename(output)
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({key: value for key, value in manifest.items() if key not in {"packages", "files"}}, indent=2), flush=True)
    finally:
        # Only this invocation's random partial archive is removed; published
        # output and any unrelated files are never cleaned up here.
        if temporary_archive.exists():
            temporary_archive.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
