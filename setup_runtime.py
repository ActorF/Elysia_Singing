"""Copy a local Elysia AI audio runtime into this project's private model cache.

This installer deliberately imports no application modules and downloads nothing.
The source checkout supplies trusted Windows binaries and dependencies; existing
destination files are never merged or overwritten. SoulX's WSL environment remains
a separately installed inference service and is not copied by this program.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile


_PROJECT_ROOT = Path(__file__).resolve().parent
_CACHE_RELATIVE = Path("models") / "cache"
_RUNTIME_NAME = "GPT-SoVITS-v2-240821"
_COPY_PATHS = (
    Path(_RUNTIME_NAME) / "runtime",
    Path(_RUNTIME_NAME) / "ffmpeg.exe",
    Path(_RUNTIME_NAME) / "ffprobe.exe",
    Path("singing-runtime") / "site-packages",
    Path("singing-runtime") / "torch",
)
_REQUIRED_FILES = (
    Path(_RUNTIME_NAME) / "runtime" / "python.exe",
    Path(_RUNTIME_NAME) / "runtime" / "python39._pth",
    Path(_RUNTIME_NAME) / "ffmpeg.exe",
    Path(_RUNTIME_NAME) / "ffprobe.exe",
    Path("singing-runtime") / "torch" / "hub" / "checkpoints" / "955717e8-8726e21a.th",
)


class _RuntimeRollbackError(RuntimeError):
    """Retain staging assets when an interrupted publication cannot fully roll back."""


def _is_link(path: Path) -> bool:
    metadata = path.lstat()
    # Junctions can escape the source/destination just like symbolic links, but
    # Path.is_symlink() does not recognize them on older supported Pythons.
    return path.is_symlink() or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _assert_local_destination(path: Path) -> None:
    try:
        path.resolve().relative_to(_PROJECT_ROOT)
    except ValueError as error:
        raise RuntimeError(f"Destination escapes the project: {path}") from error
    current = path
    while current != _PROJECT_ROOT:
        if current.exists() and _is_link(current):
            raise RuntimeError(f"Destination contains a link or junction: {current}")
        current = current.parent


def _validate_files(cache: Path) -> None:
    missing = [str(relative) for relative in _REQUIRED_FILES if not (cache / relative).is_file()]
    missing.extend(str(relative) for relative in _COPY_PATHS if not (cache / relative).exists())
    if missing:
        raise RuntimeError("Missing runtime assets:\n  " + "\n  ".join(sorted(set(missing))))


def _inventory(path: Path) -> tuple[int, int]:
    if _is_link(path):
        raise RuntimeError(f"Refusing to copy a symbolic link or junction: {path}")
    if path.is_file():
        return 1, path.stat().st_size
    files = 0
    total_bytes = 0
    for directory, child_directories, filenames in os.walk(path, followlinks=False):
        parent = Path(directory)
        for name in child_directories:
            if _is_link(parent / name):
                raise RuntimeError(f"Refusing to copy a symbolic link or junction: {parent / name}")
        for name in filenames:
            child = parent / name
            if _is_link(child):
                raise RuntimeError(f"Refusing to copy a symbolic link or junction: {child}")
            files += 1
            total_bytes += child.stat().st_size
    return files, total_bytes


def _make_embedded_python_portable(runtime: Path) -> None:
    # The original bundle enables site initialization, which reads machine-specific
    # .pth files and can execute unrelated startup hooks. Explicit local entries
    # retain its dependencies without importing those hooks or external projects.
    (runtime / "python39._pth").write_text(
        "python39.zip\n.\nLib/site-packages\n"
        "# Isolated runtime: do not enable import site or external .pth hooks.\n",
        encoding="utf-8",
    )
    stale_paths = runtime / "Lib" / "site-packages" / "users.pth"
    if stale_paths.exists():
        stale_paths.write_text(
            "# Machine-specific GPT-SoVITS source paths removed during setup.\n",
            encoding="utf-8",
        )


def _check_runtime(cache: Path) -> None:
    _validate_files(cache)
    python = cache / _RUNTIME_NAME / "runtime" / "python.exe"
    dependency_root = cache / "singing-runtime" / "site-packages"
    for relative in _COPY_PATHS:
        _assert_local_destination(cache / relative)
    diagnostic = "\n".join(
        (
            "import json, pathlib, sys",
            "root = pathlib.Path(sys.argv[1]).resolve()",
            # The application appends Demucs packages so the embedded runtime's
            # CUDA-enabled Torch remains ahead of optional CPU fallback wheels.
            "sys.path.append(str(pathlib.Path(sys.argv[2]).resolve()))",
            "import numpy, torch, torchaudio, scipy, soundfile, demucs.separate",
            "outside = [p for p in sys.path if p and not pathlib.Path(p).resolve().is_relative_to(root)]",
            "assert not outside, 'External Python search paths: ' + repr(outside)",
            "print(json.dumps({'python': sys.version.split()[0], 'numpy': numpy.__version__, "
            "'torch': torch.__version__, 'torchaudio': torchaudio.__version__, "
            "'scipy': scipy.__version__, 'cuda_available': torch.cuda.is_available()}))",
        )
    )
    environment = os.environ.copy()
    # Environment overrides are unnecessary for the isolated Windows runtime;
    # stripping them also keeps a user's global Python configuration out of checks.
    environment.pop("PYTHONHOME", None)
    environment.pop("PYTHONPATH", None)
    environment["TORCH_HOME"] = str(cache / "singing-runtime" / "torch")
    result = subprocess.run(
        [str(python), "-B", "-c", diagnostic, str(cache), str(dependency_root)],
        cwd=_PROJECT_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=120,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Runtime import check failed:\n{result.stdout}{result.stderr}")
    print("Runtime imports: " + result.stdout.strip(), flush=True)
    for executable in ("ffmpeg.exe", "ffprobe.exe"):
        result = subprocess.run(
            [str(cache / _RUNTIME_NAME / executable), "-version"],
            cwd=_PROJECT_ROOT,
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"{executable} validation failed:\n{result.stderr}")
        print(result.stdout.splitlines()[0], flush=True)


def _publish_runtime(staging: Path, destination_cache: Path) -> None:
    """Publish and validate all assets, restoring this invocation's moves on failure."""
    published: list[tuple[Path, tuple[int, int]]] = []
    try:
        for relative in _COPY_PATHS:
            source = staging / relative
            destination = destination_cache / relative
            _assert_local_destination(destination)
            if destination.exists():
                raise RuntimeError(f"Destination appeared during setup; refusing overwrite: {destination}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Record identity before rename: after publication, rollback must not
            # move a replacement that another process put at this destination.
            metadata = source.stat()
            identity = (metadata.st_dev, metadata.st_ino)
            # Register before the syscall: Ctrl+C may arrive after Windows has
            # completed the move but before Python executes the next statement.
            published.append((relative, identity))
            source.rename(destination)
        _check_runtime(destination_cache)
    except BaseException as original_error:
        rollback_errors: list[str] = []
        for relative, identity in reversed(published):
            destination = destination_cache / relative
            original = staging / relative
            try:
                _assert_local_destination(destination)
                _assert_local_destination(original)
                if original.exists():
                    original_metadata = original.stat()
                    if (original_metadata.st_dev, original_metadata.st_ino) == identity:
                        # The failed/interrupted rename never moved this asset.
                        continue
                metadata = destination.stat()
                if (metadata.st_dev, metadata.st_ino) != identity:
                    raise RuntimeError("The published path was replaced by another process.")
                if original.exists():
                    raise RuntimeError("The rollback destination already exists.")
                # Moving owned assets back preserves atomic directory visibility
                # and avoids recursively deleting files at a published location.
                destination.rename(original)
            except (OSError, RuntimeError) as rollback_error:
                rollback_errors.append(f"{relative}: {rollback_error}")
        if rollback_errors:
            raise _RuntimeRollbackError(
                f"Runtime publication failed ({original_error}); rollback needs attention. "
                f"Staged assets were retained at {staging}.\n  "
                + "\n  ".join(rollback_errors)
            ) from original_error
        raise


def _install(source_project: Path) -> None:
    source_project = source_project.expanduser().resolve()
    if (
        source_project == _PROJECT_ROOT
        or source_project in _PROJECT_ROOT.parents
        or _PROJECT_ROOT in source_project.parents
    ):
        raise RuntimeError("The source project must be separate from Elysia_Singing.")
    source_cache = source_project / _CACHE_RELATIVE
    _validate_files(source_cache)
    destination_cache = _PROJECT_ROOT / _CACHE_RELATIVE
    _assert_local_destination(destination_cache)
    for relative in _COPY_PATHS:
        target = destination_cache / relative
        _assert_local_destination(target)
        if target.exists():
            raise RuntimeError(
                f"Destination already exists: {target}\n"
                "Run setup.cmd --check to validate it. Existing assets are never overwritten."
            )
    print("Checking source files and available space...", flush=True)
    inventory = [_inventory(source_cache / relative) for relative in _COPY_PATHS]
    file_count = sum(item[0] for item in inventory)
    total_bytes = sum(item[1] for item in inventory)
    destination_cache.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(destination_cache).free < total_bytes + 256 * 1024 * 1024:
        raise RuntimeError(f"Insufficient disk space: at least {total_bytes / 1024**3:.2f} GiB is required.")
    print(f"Copying {file_count:,} files ({total_bytes / 1024**3:.2f} GiB) into this project...", flush=True)
    staging = Path(tempfile.mkdtemp(prefix=".setup-runtime-", dir=destination_cache))
    preserve_staging = False
    try:
        for relative in _COPY_PATHS:
            source = source_cache / relative
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            print(f"  {relative}", flush=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
        _make_embedded_python_portable(staging / _RUNTIME_NAME / "runtime")
        _check_runtime(staging)
        # Both staging and final-path validation belong to the transaction. A
        # late rename/import failure must leave setup retryable, not half installed.
        _publish_runtime(staging, destination_cache)
    except _RuntimeRollbackError:
        preserve_staging = True
        raise
    finally:
        # A failed rollback retains its unique staging tree for recovery. Normal
        # failures clean only this invocation's staging after reversing its moves.
        if not preserve_staging:
            _assert_local_destination(staging)
            shutil.rmtree(staging)
    print("Setup complete. Windows runtime files are local to this project.", flush=True)
    print("SoulX still requires its existing WSL environment; no models were downloaded.", flush=True)


def main() -> int:
    """Install from an explicit local project or check the existing isolated runtime."""
    if sys.version_info < (3, 10):
        print("Setup requires a standard Python 3.10 or newer.", file=sys.stderr)
        return 1
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--from-project", type=Path, help="Existing Elysia_AI checkout containing the prepared audio runtime.")
    mode.add_argument("--check", action="store_true", help="Validate local Windows dependencies without copying anything.")
    arguments = parser.parse_args()
    try:
        if os.name != "nt":
            raise RuntimeError("This setup copies the Windows audio runtime and must run on Windows.")
        if arguments.check:
            _check_runtime(_PROJECT_ROOT / _CACHE_RELATIVE)
            return 0
        source = arguments.from_project
        if source is None:
            value = input("Existing Elysia_AI project folder: ").strip().strip('"')
            if not value:
                raise RuntimeError("A source project folder is required.")
            source = Path(value)
        _install(source)
        return 0
    except (OSError, RuntimeError, subprocess.SubprocessError, EOFError) as error:
        print(f"Setup failed: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Setup interrupted. Existing destination assets were not overwritten.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
