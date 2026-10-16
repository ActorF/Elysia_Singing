"""Run the fixed Elysia full-song conversion from a Windows CMD session.

This console owns the job lifecycle formerly managed by the desktop app. All
Windows working data and final recordings are anchored to this file's project
directory, regardless of the caller's current directory. The reviewed worker
still owns audio validation, inference, sample alignment and mixing.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Iterator, Optional, Sequence
import uuid


PROJECT_ROOT = Path(__file__).resolve().parent
_EVENT_PREFIX = b"ELYSIA_SONG_COVER "
_TIMEOUT_SECONDS = 2 * 60 * 60
_MAX_LOG_BYTES = 32 * 1024 * 1024
_OUTPUT_NAMES = ("elysia-cover.wav", "elysia-cover.mp3", "generated_vocal.wav")
_EXTENSIONS = {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}


class SingingError(RuntimeError):
    """Describe a recoverable console, runtime or conversion failure."""


def parse_song_key(value: str) -> int:
    """Accept only an integer semitone offset in the inclusive -12..12 range."""
    if not re.fullmatch(r"[+-]?[0-9]{1,2}", value.strip()):
        raise ValueError("Enter an integer from -12 to 12.")
    result = int(value)
    if not -12 <= result <= 12:
        raise ValueError("Enter an integer from -12 to 12.")
    return result


def parse_yes_no(value: str) -> bool:
    """Accept explicit English yes/no answers, including the y/n abbreviations."""
    normalized = value.strip().lower()
    if normalized in {"yes", "y"}:
        return True
    if normalized in {"no", "n"}:
        return False
    raise ValueError("Enter yes or no.")


def _prompt(prompt: str, parser):
    while True:
        try:
            return parser(input(prompt))
        except ValueError as error:
            print(error)


def _song_path(value: str) -> Path:
    path = Path(value.strip().strip('"')).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in _EXTENSIONS:
        raise ValueError("Choose an existing AAC, FLAC, M4A, MP3, OGG, OPUS or WAV file.")
    if not 0 < path.stat().st_size <= 1024 * 1024 * 1024:
        raise ValueError("The audio file must be nonempty and at most 1 GiB.")
    return path


def _paths(root: Path) -> dict[str, Path]:
    native = root / "models" / "cache" / "GPT-SoVITS-v2-240821"
    separation = root / "models" / "cache" / "singing-runtime"
    return {
        "python": native / "runtime" / "python.exe",
        "worker": root / "scripts" / "song_svs_worker.py",
        "ffmpeg": native / "ffmpeg.exe",
        "ffprobe": native / "ffprobe.exe",
        "demucs-site": separation / "site-packages",
        "torch-home": separation / "torch",
    }


def _require_runtime(root: Path) -> dict[str, Path]:
    if os.name != "nt":
        raise SingingError("Conversion requires Windows with NVIDIA CUDA and the prepared WSL runtime.")
    paths = _paths(root)
    missing = [str(path.relative_to(root)) for path in paths.values() if not path.exists()]
    if missing:
        raise SingingError("Missing runtime files. Run setup.cmd first:\n  " + "\n  ".join(missing))
    return paths


def _managed_directory(root: Path, relative: str) -> Path:
    """Reject redirected output trees so results stay physically in the project."""
    destination = root / relative
    resolved = destination.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise SingingError("Project working/output directories must not point outside the project.")
    destination.mkdir(parents=True, exist_ok=True)
    return destination


@contextmanager
def _project_lock(workspace: Path) -> Iterator[None]:
    """Use an OS-held lock that is released automatically after a console crash."""
    with (workspace / "console.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise SingingError("Another conversion is already running in this project.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _environment(profile: Path, paths: dict[str, Path]) -> dict[str, str]:
    """Isolate model caches and disable implicit downloads during conversion."""
    profile.mkdir(parents=True, exist_ok=True)
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
                                      "PATHEXT", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS"}}
    environment.update({
        "PATH": os.pathsep.join((str(paths["python"].parent), str(paths["ffmpeg"].parent),
                                 str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"))),
        "USERPROFILE": str(profile), "APPDATA": str(profile), "LOCALAPPDATA": str(profile),
        "TEMP": str(profile), "TMP": str(profile), "HOME": str(profile),
        "HF_HUB_OFFLINE": "1", "HF_DATASETS_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
        "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
    })
    return environment


def build_worker_command(paths: dict[str, Path], song: Path, job: Path,
                         key: int, shift: bool) -> list[str]:
    """Build a shell-free command with fixed conversion and complete-song modes."""
    if type(key) is not int:
        raise ValueError("SongKey must be an integer.")
    parse_song_key(str(key))
    if type(shift) is not bool:
        raise ValueError("Accompaniment shift must be a boolean.")
    command = [str(paths["python"]), "-B", str(paths["worker"]),
               "--engine", "source-svc", "--source-mode", "song", "--input", str(song),
               "--key-shift-semitones", str(key), "--shift-accompaniment", str(shift).lower(),
               "--job-dir", str(job), "--job-token", job.name]
    for option in ("ffmpeg", "ffprobe", "demucs-site", "torch-home"):
        command.extend(("--" + option, str(paths[option])))
    return command


def _stop_process(process: subprocess.Popen) -> None:
    """Stop the native worker tree before requesting exact-UUID WSL cleanup."""
    if process.poll() is not None:
        return
    if os.name == "nt":
        taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        subprocess.run([str(taskkill), "/PID", str(process.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
    else:
        process.kill()
    process.wait(timeout=30)


def _cleanup(paths: dict[str, Path], token: str, root: Path, environment: dict[str, str]) -> None:
    command = [str(paths["python"]), "-B", str(paths["worker"]), "--cleanup-private-job", token]
    cleanup = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    try:
        stdout, _ = cleanup.communicate(timeout=60)
    except BaseException:
        _stop_process(cleanup)
        raise SingingError("WSL cleanup could not finish; working files were retained for recovery.") from None
    if cleanup.returncode or stdout.strip() != b'ELYSIA_SONG_COVER_CLEANUP {"status":"complete"}':
        raise SingingError("WSL cleanup failed; working files were retained. Run start.cmd --recover.")


def _stream_worker(process: subprocess.Popen, log_path: Path) -> None:
    """Consume real stage events while bounding logs and enforcing a wall deadline."""
    events: queue.Queue = queue.Queue(maxsize=128)

    def _read() -> None:
        try:
            while True:
                line = process.stdout.readline(65537)
                if not line:
                    break
                events.put(line)
        finally:
            events.put(None)

    threading.Thread(target=_read, daemon=True).start()
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    total = 0
    complete = False
    last_message = ""
    with log_path.open("wb") as log:
        while True:
            if time.monotonic() >= deadline:
                raise SingingError("Conversion exceeded the two-hour limit.")
            try:
                line = events.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                break
            total += len(line)
            if total > _MAX_LOG_BYTES or len(line) > 65536:
                raise SingingError("Worker diagnostics exceeded the size limit.")
            log.write(line)
            log.flush()
            if not line.startswith(_EVENT_PREFIX):
                continue
            try:
                event = json.loads(line[len(_EVENT_PREFIX):])
            except (ValueError, UnicodeError) as error:
                raise SingingError("The worker returned an invalid progress event.") from error
            if (not isinstance(event, dict)
                    or set(event) != {"message", "progressPercent", "stage"}
                    or type(event["progressPercent"]) is not int
                    or not 0 <= event["progressPercent"] <= 100
                    or not isinstance(event["message"], str)
                    or event["stage"] not in ("validating", "separating", "converting", "mixing", "complete")
                    or complete):
                raise SingingError("The worker returned an invalid progress event.")
            if event.get("stage") == "complete":
                if event["progressPercent"] != 100:
                    raise SingingError("The worker returned an incomplete final event.")
                complete = True
            else:
                message = f"[{event['progressPercent']:3d}%] {event['message']}"
                if message != last_message:
                    print(message, flush=True)
                    last_message = message
    status = process.wait(timeout=max(1, deadline - time.monotonic()))
    if status != 0 or not complete:
        raise SingingError(f"Conversion failed (exit {status}). See local log: {log_path}")


def _recover(root: Path, paths: dict[str, Path], workspace: Path) -> None:
    """Recover only recorded incomplete jobs; successful outputs are never scanned."""
    for marker in sorted(workspace.glob("pending-*.json")):
        record = json.loads(marker.read_text(encoding="utf-8"))
        token = record.get("job_id") if isinstance(record, dict) else None
        if (not isinstance(token, str)
                or not re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", token)
                or marker.name != f"pending-{token}.json"):
            raise SingingError("An invalid recovery marker requires inspection: " + str(marker))
        # A console closed from its title bar can leave the native worker alive.
        # Refuse cleanup while its PID exists rather than risk deleting live data
        # or terminating an unrelated process after Windows has reused the PID.
        pid = record.get("pid")
        if pid is not None and (type(pid) is not int or pid <= 0):
            raise SingingError("An invalid worker PID requires inspection: " + str(marker))
        if pid and _pid_exists(pid):
            raise SingingError(f"Previous worker PID {pid} is still active. Let it finish, then run --recover.")
        _cleanup(paths, token, root, _environment(workspace / "profiles" / token, paths))
        marker.unlink()
        print(f"Recovered incomplete job {token}; local files retained in workspace/jobs.")


def _pid_exists(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        handle = kernel.OpenProcess(0x1000, False, pid)
        if handle:
            try:
                status = wintypes.DWORD()
                return not kernel.GetExitCodeProcess(handle, ctypes.byref(status)) or status.value == 259
            finally:
                kernel.CloseHandle(handle)
        return ctypes.get_last_error() != 87
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def run_conversion(root: Path, song: Path, key: int, shift: bool) -> Path:
    """Run one exclusive full-song job and retain its outputs inside this project."""
    if type(key) is not int or not -12 <= key <= 12 or type(shift) is not bool:
        raise ValueError("Use an integer SongKey from -12 to 12 and an explicit accompaniment boolean.")
    paths = _require_runtime(root)
    workspace = _managed_directory(root, "workspace")
    outputs = _managed_directory(root, "output")
    with _project_lock(workspace):
        _recover(root, paths, workspace)
        token = str(uuid.uuid4())
        job = _managed_directory(root, "workspace/jobs") / token
        job.mkdir()
        (job / "singing-request.json").write_text(json.dumps({
            "schema_version": 1, "reference": "elysia", "key_shift_semitones": key,
        }), encoding="utf-8")
        profile = _managed_directory(root, "workspace/profiles") / token
        environment = _environment(profile, paths)
        log = _managed_directory(root, "workspace/logs") / (token + ".log")
        marker = workspace / f"pending-{token}.json"
        _write_marker(marker, {"job_id": token})
        process = None
        try:
            process = subprocess.Popen(build_worker_command(paths, song, job, key, shift),
                                       cwd=root, env=environment, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            _write_marker(marker, {"job_id": token, "pid": process.pid})
            _stream_worker(process, log)
            if {p.name for p in job.iterdir()} != set(_OUTPUT_NAMES):
                raise SingingError("The conversion did not produce the expected final audio files.")
            destination = outputs / token
            job.rename(destination)
            marker.unlink()
            return destination
        except BaseException:
            if process is not None:
                try:
                    _stop_process(process)
                except (OSError, subprocess.SubprocessError) as error:
                    # The worker may still own its WSL job. Retain its marker
                    # and never remove files before native termination is proven.
                    raise SingingError("Could not stop the worker; recovery state was retained. "
                                       "Wait for it to exit, then run start.cmd --recover.") from error
            _cleanup(paths, token, root, environment)
            marker.unlink()
            raise


def _write_marker(path: Path, record: dict) -> None:
    """Replace recovery state atomically so a crash cannot leave truncated JSON."""
    staged = path.with_suffix(".tmp")
    with staged.open("w", encoding="utf-8") as handle:
        json.dump(record, handle)
        handle.flush()
        os.fsync(handle.fileno())
    staged.replace(path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Prompt for song, key and accompaniment, or run explicit noninteractive flags."""
    parser = argparse.ArgumentParser(description="Elysia full-song voice conversion (CMD).")
    parser.add_argument("song", nargs="?", help="Complete song path; quotes and drag-and-drop are supported.")
    parser.add_argument("--song-key", type=parse_song_key)
    parser.add_argument("--shift-accompaniment", type=parse_yes_no, metavar="yes|no")
    parser.add_argument("--recover", action="store_true", help="Clean up interrupted WSL jobs and exit.")
    args = parser.parse_args(argv)
    print("Elysia Singing | Voice conversion | Elysia | Entire vocal track | Complete song")
    try:
        if args.recover:
            paths = _require_runtime(PROJECT_ROOT)
            workspace = _managed_directory(PROJECT_ROOT, "workspace")
            with _project_lock(workspace):
                _recover(PROJECT_ROOT, paths, workspace)
            print("Recovery complete.")
            return 0
        song = _song_path(args.song) if args.song else _prompt("Complete song path (or drag file here): ", _song_path)
        key = args.song_key if args.song_key is not None else _prompt("SongKey (-12 to 12): ", parse_song_key)
        shift = (args.shift_accompaniment if args.shift_accompaniment is not None
                 else _prompt("Shift accompaniment with vocal (yes/no): ", parse_yes_no))
        print(f"SongKey: {key:+d} | Shift accompaniment: {'yes' if shift else 'no'}")
        destination = run_conversion(PROJECT_ROOT, song, key, shift)
        print("Complete. Output directory: " + str(destination))
        for name in _OUTPUT_NAMES:
            print("  " + str(destination / name))
        return 0
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled.", file=sys.stderr)
        return 130
    except (SingingError, ValueError, OSError, subprocess.SubprocessError) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
