"""Exercise the release bootstrap with PowerShell 5.1 and tiny offline fixtures.

Only helper definitions are loaded, so tests never download dependencies,
register WSL distributions, change machine settings or run model inference.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import unittest


_SCRIPT = Path(__file__).resolve().parents[1] / "bootstrap.ps1"
_POWERSHELL = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
_REQUIRED_NATIVE = (
    "models/cache/GPT-SoVITS-v2-240821/runtime/python.exe",
    "models/cache/GPT-SoVITS-v2-240821/runtime/python39.dll",
    "models/cache/GPT-SoVITS-v2-240821/runtime/python39.zip",
    "models/cache/GPT-SoVITS-v2-240821/runtime/python39._pth",
    "models/cache/GPT-SoVITS-v2-240821/runtime/Lib/site-packages/torch/__init__.py",
    "models/cache/GPT-SoVITS-v2-240821/ffmpeg.exe",
    "models/cache/GPT-SoVITS-v2-240821/ffprobe.exe",
    "models/cache/singing-runtime/site-packages/demucs/separate.py",
    "models/cache/singing-runtime/torch/hub/checkpoints/955717e8-8726e21a.th",
)


def _ps_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@unittest.skipUnless(_POWERSHELL.is_file(), "Windows PowerShell 5.1 is required")
class BootstrapTests(unittest.TestCase):
    """Verify archive integrity and publication without touching a real runtime."""

    def setUp(self) -> None:
        """Allocate a fresh fake project for every bootstrap operation."""
        temporary = tempfile.TemporaryDirectory(prefix="elysia-bootstrap-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.parts = self.root / "parts"
        self.parts.mkdir()

    def _run(self, command: str) -> subprocess.CompletedProcess:
        script = f"""
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$ProjectRoot = {_ps_literal(self.root)}
$Repository = 'ActorF/Elysia_Singing'
$BundleDirectory = {_ps_literal(self.parts)}
$TarExecutable = Join-Path $env:SystemRoot 'System32\\tar.exe'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile({_ps_literal(_SCRIPT)}, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) {{ throw ($parseErrors | Out-String) }}
foreach ($definition in $ast.FindAll({{param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst]}}, $false)) {{
    . ([scriptblock]::Create($definition.Extent.Text))
}}
{command}
exit 0
"""
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run(
            [str(_POWERSHELL), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def _manifest(self, content: bytes) -> dict:
        parts = []
        for index, start in enumerate(range(0, len(content), 7)):
            data = content[start:start + 7]
            name = f"windows-runtime.tar.gz.part{index:03d}"
            (self.parts / name).write_bytes(data)
            parts.append({"name": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
        bundle = {"archive": "windows-runtime.tar.gz", "size": len(content),
                  "sha256": hashlib.sha256(content).hexdigest(), "parts": parts}
        linux = dict(bundle, archive="linux-runtime.tar.gz")
        manifest = {"schema_version": 1, "release_tag": "v0.1.0", "repository": "ActorF/Elysia_Singing",
                    "bundles": {"windows": bundle, "linux": linux}}
        (self.root / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def _archive(self, names: tuple[str, ...]) -> Path:
        archive = self.root / "windows-runtime.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            for name in names:
                info = tarfile.TarInfo(name)
                info.size = 1
                output.addfile(info, io.BytesIO(b"x"))
        return archive

    def test_manifest_and_local_parts_reassemble_exact_bytes(self) -> None:
        """Authenticated multipart downloads reconstruct in the declared order."""
        content = b"deterministic release fixture with three or more chunks"
        self._manifest(content)
        self._run("$m = _Read-Manifest; _Get-Archive $m.bundles.windows $m.release_tag | Out-Null")
        self.assertEqual((self.root / "models/downloads/windows-runtime.tar.gz").read_bytes(), content)

    def test_corrupt_part_never_publishes_an_archive(self) -> None:
        """A local part with the right size but wrong digest fails before assembly."""
        manifest = self._manifest(b"some trusted release data")
        part = manifest["bundles"]["windows"]["parts"][0]
        (self.parts / part["name"]).write_bytes(b"z" * part["size"])
        self._run("$m = _Read-Manifest; try { _Get-Archive $m.bundles.windows $m.release_tag; throw 'accepted corrupt part' } catch { if ($_.Exception.Message -notlike 'Missing or invalid local release part:*') { throw } }")
        self.assertFalse((self.root / "models/downloads/windows-runtime.tar.gz").exists())

    def test_manifest_rejects_remote_repository_changes(self) -> None:
        """An edited manifest cannot redirect runtime downloads to another owner."""
        manifest = self._manifest(b"release")
        manifest["repository"] = "other/repository"
        (self.root / "release-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self._run("try { _Read-Manifest; throw 'accepted wrong repository' } catch { if ($_.Exception.Message -notlike '*not a supported*') { throw } }")

    def test_complete_native_cache_is_published_together(self) -> None:
        """A verified archive appears as one complete runtime directory."""
        archive = self._archive(_REQUIRED_NATIVE)
        self._run(f"_Install-Native {_ps_literal(archive)}; if (-not (_Native-Ready $ProjectRoot)) {{ throw 'missing native files' }}")
        self.assertTrue(all((self.root / name).is_file() for name in _REQUIRED_NATIVE))
        self.assertEqual(list((self.root / "models").glob(".native-stage-*")), [])

    def test_incomplete_native_archive_leaves_no_published_cache(self) -> None:
        """Failure before publication leaves installation retryable."""
        archive = self._archive(_REQUIRED_NATIVE[:2])
        self._run(f"try {{ _Install-Native {_ps_literal(archive)}; throw 'accepted incomplete archive' }} catch {{ if ($_.Exception.Message -notlike '*does not contain the complete*') {{ throw }} }}")
        self.assertFalse((self.root / "models/cache").exists())
        self.assertEqual(list((self.root / "models").glob(".native-stage-*")), [])

    def test_windows_archive_rejects_paths_outside_cache(self) -> None:
        """Archive members cannot write into unrelated project files."""
        archive = self._archive(("main.py",))
        self._run(f"try {{ _Install-Native {_ps_literal(archive)}; throw 'accepted foreign archive member' }} catch {{ if ($_.Exception.Message -notlike 'Unexpected Windows archive path:*') {{ throw }} }}")
        self.assertFalse((self.root / "main.py").exists())

    def test_state_write_replaces_valid_json_atomically(self) -> None:
        """Repeated setup can record its owned distribution without partial JSON."""
        self._run("_ProjectDirectory 'models' | Out-Null; _Write-State 'Elysia-Singing-test' 'v0.1.0'; _Write-State 'Elysia-Singing-new' 'v0.1.0'")
        state = json.loads((self.root / "models/runtime-state.json").read_text(encoding="utf-8"))
        self.assertEqual(state, {"schema_version": 1, "distribution": "Elysia-Singing-new", "release_tag": "v0.1.0"})
        self.assertEqual(list((self.root / "models").glob("*.tmp")), [])

    def test_existing_foreign_distribution_is_not_queried_or_changed(self) -> None:
        """A matching distro name cannot take ownership of another folder's data."""
        self._run("function _Distribution-Record { [pscustomobject]@{Version=2;BasePath='C:\\another-project'} }; try { _Verify-Distribution 'Elysia-Singing-test' (Join-Path $ProjectRoot 'models\\wsl') 'v0.1.0'; throw 'accepted foreign distribution' } catch { if ($_.Exception.Message -notlike '*already registered outside*') { throw } }")

    def test_setup_obeys_the_active_python_console_lock(self) -> None:
        """PowerShell setup and the Python converter share the same OS byte lock."""
        import msvcrt

        self._manifest(b"release")
        script = self.root / "bootstrap.ps1"
        shutil.copyfile(_SCRIPT, script)
        workspace = self.root / "workspace"
        workspace.mkdir()
        with (workspace / "console.lock").open("w+b") as lock:
            lock.write(b"0")
            lock.flush()
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                result = subprocess.run(
                    [str(_POWERSHELL), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-File", str(script), "-CheckOnly"],
                    capture_output=True, text=True, timeout=30,
                )
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("already running in this project", result.stdout)
        self.assertFalse((self.root / "models").exists())


if __name__ == "__main__":
    unittest.main()
