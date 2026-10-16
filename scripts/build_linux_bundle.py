"""Build a clean distributable WSL rootfs containing only the singing runtime.

Run this release-maintainer tool as root inside Linux/WSL. It starts from a
SHA-pinned Ubuntu Base image, installs system dependencies in an isolated
chroot, and copies an explicit allowlist from a prepared SoulX runtime. It
never exports the maintainer's distribution or copies home directories,
credentials, personal prompt banks, source songs, or inference jobs.

The inference environment and public source checkout retain their package
licenses. Absolute environment links are relocated to the fixed release user.
Phase markers permit retrying download, installation, and archive generation
without touching the original runtime. Runtime copy is staged before commit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import urllib.request


_BASE_NAME = "ubuntu-base-24.04.5-base-amd64.tar.gz"
_BASE_URL = "https://cdimages.ubuntu.com/ubuntu-base/releases/24.04/release/" + _BASE_NAME
_BASE_SHA256 = "e77b6f10c2590cef872b33ee9f635a0e3fd1f57fb074c0e52b5c7f56147a0c86"
_RELEASE = "v0.1.0"
_RUNTIME_RELATIVE = Path("home/elysia/.local/share/elysia-ai/soulx")
_TARGET_RUNTIME = Path("/") / _RUNTIME_RELATIVE
_PYTHON_RELATIVE = Path("python/cpython-3.10.22-linux-x86_64-gnu")
_FFMPEG_RELATIVE = Path("prep-env/lib/python3.10/site-packages/imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2")
_SOURCE_REVISION = "81aeb3ae772c70093c3de74dc23c92d983801ae4"
_SYSTEM_PACKAGES = (
    "ca-certificates", "git", "python3", "libgomp1", "libsndfile1",
    "libstdc++6", "libatomic1", "passwd", "gzip",
)


def _run(arguments: list[str], *, timeout: int = 1800) -> str:
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(f"Command failed ({arguments[0]}):\n{result.stdout[-6000:]}")
    return result.stdout


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _remove_inside(path: Path, parent: Path) -> None:
    """Remove only verified staging descendants, without following symlinks."""
    if path.is_symlink():
        path.parent.resolve().relative_to(parent.resolve())
        path.unlink()
    elif path.exists():
        resolved = path.resolve()
        resolved.relative_to(parent.resolve())
        if resolved == parent.resolve():
            raise ValueError("Refusing to delete a staging root.")
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def _download_base(staging: Path) -> Path:
    archive = staging / _BASE_NAME
    if not archive.exists():
        print("Downloading the clean Ubuntu Base image...", flush=True)
        temporary = archive.with_suffix(".download")
        with urllib.request.urlopen(_BASE_URL, timeout=60) as source, temporary.open("wb") as destination:
            shutil.copyfileobj(source, destination, 1024 * 1024)
        temporary.replace(archive)
    if _sha256(archive) != _BASE_SHA256:
        raise ValueError("Ubuntu Base archive does not match the pinned release digest.")
    return archive


def _unpack_base(staging: Path, archive: Path) -> Path:
    rootfs = staging / "rootfs"
    if (staging / "base.complete").exists():
        if not (rootfs / "etc/os-release").is_file():
            raise ValueError("Completed base marker has no valid rootfs.")
        return rootfs
    if rootfs.exists():
        raise ValueError("An incomplete rootfs exists; inspect it before choosing a fresh staging directory.")
    rootfs.mkdir(mode=0o755)
    # Only this pinned official image is extracted with root ownership and
    # native symlink semantics. User-controlled archives are never accepted.
    _run(["/usr/bin/tar", "-xzf", str(archive), "--numeric-owner", "-C", str(rootfs)])
    (staging / "base.complete").write_text(_BASE_SHA256 + "\n", encoding="ascii")
    return rootfs


def _chroot(rootfs: Path, arguments: list[str], *, timeout: int = 1800) -> str:
    return _run(["/usr/sbin/chroot", str(rootfs), "/usr/bin/env",
                 "DEBIAN_FRONTEND=noninteractive", "LC_ALL=C.UTF-8", *arguments], timeout=timeout)


def _prepare_devices(rootfs: Path) -> None:
    """Create build-only standard devices without binding the host's /dev tree."""
    for name, major, minor in (("null", 1, 3), ("zero", 1, 5), ("random", 1, 8), ("urandom", 1, 9)):
        target = rootfs / "dev" / name
        if not target.exists():
            os.mknod(target, stat.S_IFCHR | 0o666, os.makedev(major, minor))
        # A maintainer's restrictive umask must not block apt's sandbox user
        # from standard devices while signatures are checked in the chroot.
        target.chmod(0o666)


def _install_system(staging: Path, rootfs: Path) -> None:
    if (staging / "system.complete").exists():
        return
    print("Installing system dependencies in the clean chroot...", flush=True)
    _prepare_devices(rootfs)
    resolv = rootfs / "etc/resolv.conf"
    if resolv.is_symlink():
        resolv.unlink()
    resolv.write_text(Path("/etc/resolv.conf").read_text(), encoding="utf-8")
    policy = rootfs / "usr/sbin/policy-rc.d"
    policy.write_text("#!/bin/sh\n# Build-time policy: services must not start inside the release chroot.\nexit 101\n", encoding="ascii")
    policy.chmod(0o755)
    _chroot(rootfs, ["/usr/bin/apt-get", "update"])
    _chroot(rootfs, ["/usr/bin/apt-get", "install", "-y", "--no-install-recommends", *_SYSTEM_PACKAGES])
    _chroot(rootfs, ["/usr/bin/apt-get", "clean"])
    # Dedicated release identity contains no imported password or account data.
    accounts = (rootfs / "etc/passwd").read_text()
    if not any(line.startswith("elysia:") for line in accounts.splitlines()):
        _chroot(rootfs, ["/usr/sbin/useradd", "--uid", "1000", "--user-group", "--create-home", "--shell", "/bin/bash", "elysia"])
    (rootfs / "etc/wsl.conf").write_text("[user]\ndefault=elysia\n[interop]\nappendWindowsPath=false\n", encoding="ascii")
    (rootfs / "etc/elysia-singing-release").write_text(_RELEASE + "\n", encoding="ascii")
    wslpath = rootfs / "usr/bin/wslpath"
    if not wslpath.exists() and not wslpath.is_symlink():
        wslpath.symlink_to("/init")
    (staging / "system.complete").write_text(_RELEASE + "\n", encoding="ascii")


def _verify_source_assets(source: Path, project: Path) -> tuple:
    # Import only the local project's static asset table. No model packages or
    # original application modules are imported by the builder.
    sys.path.insert(0, str(project))
    from scripts.song_svc_assets import _ASSETS
    from scripts.song_svs_runtime import _CORE_RUNTIME_ASSETS

    specifications = tuple((relative, size, digest) for _, relative, size, digest in _ASSETS)
    specifications += tuple((item.relative_path, item.expected_bytes, item.expected_sha256) for item in _CORE_RUNTIME_ASSETS[:2])
    for relative, size, digest in specifications:
        path = source / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size != size or _sha256(path) != digest:
            raise ValueError(f"Source runtime asset failed verification: {relative}")
    git_arguments = ["/usr/bin/git", "-c", "safe.directory=" + str(source / "source"), "-C", str(source / "source")]
    revision = _run([*git_arguments, "rev-parse", "HEAD"]).strip()
    dirty = _run([*git_arguments, "status", "--porcelain=v1", "--untracked-files=all"])
    if revision != _SOURCE_REVISION or dirty.strip():
        raise ValueError("Source checkout must be the clean, pinned public SoulX revision.")
    return specifications


def _copy_tree(source: Path, target: Path) -> None:
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"))


def _relocate_environment(runtime: Path, source_runtime: Path) -> None:
    """Rewrite only deployment paths, preserving installed package payloads."""
    for base, directories, files in os.walk(runtime, followlinks=False):
        for name in directories + files:
            path = Path(base) / name
            if path.is_symlink():
                target = os.readlink(path)
                if target.startswith(str(source_runtime) + "/"):
                    path.unlink()
                    path.symlink_to(str(_TARGET_RUNTIME) + target[len(str(source_runtime)):])
                elif target.startswith("/"):
                    raise ValueError(f"Unexpected absolute runtime link: {path.relative_to(runtime)}")
    for environment in ("infer-env", "prep-env"):
        env_root = runtime / environment
        config = env_root / "pyvenv.cfg"
        if config.exists():
            config.write_text(config.read_text().replace(str(source_runtime), str(_TARGET_RUNTIME)), encoding="utf-8")
        for entry in (env_root / "bin").iterdir():
            if entry.is_symlink() or not entry.is_file():
                continue
            raw = entry.read_bytes()
            if raw.startswith(b"#!") or entry.name.startswith("activate"):
                entry.write_bytes(raw.replace(str(source_runtime).encode(), str(_TARGET_RUNTIME).encode()))


def _copy_runtime(staging: Path, rootfs: Path, source: Path, project: Path) -> None:
    if (staging / "runtime.complete").exists():
        return
    print("Verifying and copying the allowlisted inference runtime...", flush=True)
    assets = _verify_source_assets(source, project)
    runtime = rootfs / _RUNTIME_RELATIVE
    pending = rootfs / _RUNTIME_RELATIVE.parent / "soulx.building"
    pending.parent.mkdir(parents=True, exist_ok=True)
    _remove_inside(pending, rootfs)
    pending.mkdir(mode=0o700)
    _copy_tree(source / _PYTHON_RELATIVE, pending / _PYTHON_RELATIVE)
    _copy_tree(source / "infer-env", pending / "infer-env")
    _copy_tree(source / "source", pending / "source")
    # Preserve a valid clean checkout while dropping maintainer-local Git
    # settings and reflogs. Public commit objects and tracked files stay intact.
    git_root = pending / "source/.git"
    (git_root / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tlogallrefupdates = false\n"
        "[remote \"origin\"]\n\turl = https://github.com/Soul-AILab/SoulX-Singer.git\n"
        "\tfetch = +refs/heads/*:refs/remotes/origin/*\n", encoding="ascii")
    for name in ("logs", "hooks", "FETCH_HEAD", "ORIG_HEAD", "COMMIT_EDITMSG"):
        _remove_inside(git_root / name, rootfs)
    for relative, _size, _digest in assets:
        if relative.startswith("python/"):
            continue
        target = pending / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / relative, target)
    # The bridge needs stdlib only. Keeping the original 7.6 GB transcription
    # environment would distribute unused ASR/CUDA packages and alignment code.
    prep = pending / "prep-env"
    (prep / "bin").mkdir(parents=True, exist_ok=True)
    (prep / "bin/python").symlink_to(str(source / _PYTHON_RELATIVE / "bin/python3.10"))
    (prep / "pyvenv.cfg").write_text(
        f"home = {source / _PYTHON_RELATIVE / 'bin'}\nimplementation = CPython\n"
        "version_info = 3.10.22\ninclude-system-site-packages = false\n", encoding="ascii")
    # The inherited source integrity check expects both canonical model links;
    # Singer's directory exists empty because its weights are never used by SVC.
    (pending / "models/SoulX-Singer").mkdir(parents=True, exist_ok=True)
    (pending / "jobs").mkdir(mode=0o700)
    _relocate_environment(pending, source)
    for base, directories, files in os.walk(pending, followlinks=False):
        for name in directories + files:
            path = Path(base) / name
            os.chown(path, 1000, 1000, follow_symlinks=False)
            if not path.is_symlink():
                path.chmod(path.stat().st_mode & ~0o022)
    os.chown(pending, 1000, 1000)
    private_prompts = pending / "prompts/singing-v1"
    private_prompts.chmod(0o700)
    for path in private_prompts.iterdir():
        path.chmod(0o600)
    for ancestor in pending.parents:
        if ancestor == rootfs / "home":
            break
        os.chown(ancestor, 1000, 1000)
        ancestor.chmod(0o755)
    if runtime.exists():
        raise ValueError("Final runtime already exists without a completed build marker.")
    pending.rename(runtime)
    (staging / "runtime.complete").write_text(_RELEASE + "\n", encoding="ascii")


def _verify_chroot(rootfs: Path, project: Path) -> None:
    print("Checking relocated Python environments and public source integrity...", flush=True)
    _prepare_devices(rootfs)
    runtime = str(_TARGET_RUNTIME)
    _chroot(rootfs, ["/usr/bin/python3", "-B", "-I", "-c", "import pwd; assert pwd.getpwnam('elysia').pw_uid == 1000"])
    _chroot(rootfs, [runtime + "/prep-env/bin/python", "-B", "-I", "-c", "import json, pathlib, pwd, subprocess; print('bridge ready')"])
    result = _chroot(rootfs, [runtime + "/infer-env/bin/python", "-B", "-I", "-c",
        "import torch, torchaudio, numpy, scipy, librosa, soundfile, transformers, accelerate, omegaconf, numba; "
        "assert torch.__version__ == '2.2.0+cu121'; print('inference imports ready')"])
    print(result.strip(), flush=True)
    source = runtime + "/source"
    status = _chroot(rootfs, ["/usr/bin/git", "-c", "safe.directory=" + source, "-C", source,
                             "status", "--porcelain=v1", "--untracked-files=all"])
    if status.strip():
        raise ValueError("Relocated public source checkout is not clean.")
    verification = rootfs / "tmp/elysia-release-verification"
    _remove_inside(verification, rootfs)
    (verification / "scripts").mkdir(parents=True)
    for script in (project / "scripts").glob("song_*.py"):
        shutil.copy2(script, verification / "scripts" / script.name)
    shutil.copy2(project / "scripts/__init__.py", verification / "scripts/__init__.py")
    try:
        # Validate as the actual release identity, not root, so private prompt
        # ownership and interpreter/source-link checks exercise production rules.
        result = _run(["/usr/sbin/chroot", "--userspec=1000:1000", str(rootfs),
            "/usr/bin/env", "HOME=/home/elysia", runtime + "/prep-env/bin/python", "-B", "-c",
            "import sys; sys.path.insert(0, '/tmp/elysia-release-verification'); "
            "from scripts import song_svs_runtime as runtime; "
            "from scripts.song_svc_assets import verify_svc_assets; "
            "root=runtime._runtime_root(); verify_svc_assets(root); "
            "runtime._verify_source_checkout(root); print('release asset boundary ready')"])
        print(result.strip(), flush=True)
    finally:
        _remove_inside(verification, rootfs)


def _retain_ffmpeg_notices(rootfs: Path, source: Path) -> None:
    """Retain package attribution beside the allowlisted Linux FFmpeg binary."""
    site = Path("prep-env/lib/python3.10/site-packages")
    for metadata in (source / site).glob("imageio_ffmpeg-*.dist-info"):
        for name in ("LICENSE", "METADATA"):
            origin = metadata / name
            if origin.is_file():
                destination = rootfs / _RUNTIME_RELATIVE / site / metadata.name / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(origin, destination)
                os.chown(destination.parent, 1000, 1000)
                os.chown(destination, 1000, 1000)


def _sanitize_system(rootfs: Path) -> None:
    """Drop build-only networking state and package caches, keeping licenses."""
    for name in ("etc/resolv.conf", "etc/machine-id", "usr/sbin/policy-rc.d"):
        _remove_inside(rootfs / name, rootfs)
    (rootfs / "etc/machine-id").write_text("", encoding="ascii")
    for name in ("var/lib/apt/lists", "var/cache/apt/archives", "var/log", "tmp", "root"):
        directory = rootfs / name
        if directory.is_dir():
            for child in directory.iterdir():
                _remove_inside(child, rootfs)
    # WSL supplies /init and device nodes on import; do not archive build-time
    # devices, since a source tar should contain portable filesystem entries.
    for child in (rootfs / "dev").iterdir():
        _remove_inside(child, rootfs)
    runtime = rootfs / _RUNTIME_RELATIVE
    for base, directories, _files in os.walk(runtime, followlinks=False):
        if "__pycache__" in directories:
            _remove_inside(Path(base) / "__pycache__", rootfs)
            directories.remove("__pycache__")


def _archive(rootfs: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".building")
    print("Compressing the clean Linux runtime release...", flush=True)
    with temporary.open("wb") as destination:
        tar_process = subprocess.Popen(["/usr/bin/tar", "--numeric-owner", "-C", str(rootfs), "-cf", "-", "."], stdout=subprocess.PIPE)
        assert tar_process.stdout is not None
        compressor = subprocess.Popen(["/usr/bin/gzip", "-1", "-n"], stdin=tar_process.stdout, stdout=destination)
        tar_process.stdout.close()
        gzip_status = compressor.wait()
        tar_status = tar_process.wait()
    if gzip_status or tar_status:
        raise RuntimeError("Release archive compression failed.")
    temporary.replace(output)
    manifest = {"release": _RELEASE, "file": output.name, "bytes": output.stat().st_size,
                "sha256": _sha256(output), "base_sha256": _BASE_SHA256,
                "source_revision": _SOURCE_REVISION}
    output.with_suffix(output.suffix + ".json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest), flush=True)


def main() -> int:
    """Build a clean WSL release from verified local assets without exporting a distro."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-runtime", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("Run this maintainer tool as root in Linux/WSL.")
    source = args.source_runtime.resolve(strict=True)
    staging = args.staging.resolve()
    output = args.output.resolve()
    if len(staging.parts) < 4 or staging == source or staging.is_relative_to(source) or source.is_relative_to(staging):
        parser.error("Staging must be a separate dedicated directory outside the source runtime.")
    if output == source or output.is_relative_to(source) or output.is_relative_to(staging / "rootfs"):
        parser.error("The release archive must be outside the original runtime and staged rootfs.")
    staging.mkdir(parents=True, exist_ok=True)
    rootfs = _unpack_base(staging, _download_base(staging))
    _install_system(staging, rootfs)
    _copy_runtime(staging, rootfs, source, Path(__file__).resolve().parents[1])
    _retain_ffmpeg_notices(rootfs, source)
    _verify_chroot(rootfs, Path(__file__).resolve().parents[1])
    _sanitize_system(rootfs)
    _archive(rootfs, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
