"""Check split-release integrity and exclusive selection of the bundled WSL runtime."""

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import package_release
from scripts import song_svs_worker as worker


class DistributionTests(unittest.TestCase):
    """Prevent missing archive bytes and accidental use of a user's default distro."""

    def test_parts_reassemble_and_match_every_manifest_digest(self) -> None:
        """A partial last chunk retains every byte and has independently valid metadata."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = bytes(range(57))
            archive = root / "example.tar.gz"
            archive.write_bytes(original)
            with patch.object(package_release, "_PART_BYTES", 16), patch.object(package_release, "_BLOCK_BYTES", 5):
                manifest = package_release.split_archive(archive, root)
            assembled = b""
            for part in manifest["parts"]:
                content = (root / part["name"]).read_bytes()
                self.assertEqual(len(content), part["size"])
                self.assertLessEqual(len(content), 16)
                self.assertEqual(hashlib.sha256(content).hexdigest(), part["sha256"])
                assembled += content
            self.assertEqual(assembled, original)
            self.assertEqual(manifest["size"], len(original))
            self.assertEqual(manifest["sha256"], hashlib.sha256(original).hexdigest())

    def test_worker_always_names_its_provisioned_distribution_and_user(self) -> None:
        """Changing Windows' default WSL distro cannot redirect model execution."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            receipt = root / "models/runtime-state.json"
            receipt.write_text(json.dumps({"schema_version": 1,
                                           "distribution": "Elysia-Singing-012345abcdef"}), encoding="utf-8")
            with patch.object(worker, "__file__", str(root / "scripts/song_svs_worker.py")):
                self.assertEqual(worker._wsl_command(Path("wsl.exe")),
                                 ["wsl.exe", "--distribution", "Elysia-Singing-012345abcdef", "--user", "elysia"])
                receipt.unlink()
                with self.assertRaises(worker._SvsWorkerFailure):
                    worker._wsl_command(Path("wsl.exe"))

    def test_worker_rejects_unrelated_distribution_in_receipt(self) -> None:
        """A hand-edited state file cannot select an unrelated ordinary distro."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "models").mkdir()
            (root / "models/runtime-state.json").write_text(json.dumps({"schema_version": 1,
                                                                       "distribution": "Ubuntu"}), encoding="utf-8")
            with patch.object(worker, "__file__", str(root / "scripts/song_svs_worker.py")):
                with self.assertRaises(worker._SvsWorkerFailure):
                    worker._wsl_command(Path("wsl.exe"))


if __name__ == "__main__":
    unittest.main()
