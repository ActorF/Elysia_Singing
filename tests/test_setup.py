"""Verify runtime setup rollback and ownership without touching the real cache."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

import setup_runtime as setup


class RuntimePublicationTests(unittest.TestCase):
    """Exercise failures between asset moves and after final runtime validation."""

    def setUp(self) -> None:
        """Build tiny staged assets inside an isolated replacement project root."""
        temporary = tempfile.TemporaryDirectory(prefix="elysia-setup-test-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.cache = self.root / "models" / "cache"
        self.staging = self.cache / ".setup-runtime-test"
        self.staging.mkdir(parents=True)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(setup, "_PROJECT_ROOT", self.root).start()
        self.paths = (Path("runtime") / "packages", Path("runtime") / "ffmpeg.exe", Path("audio") / "model")
        mock.patch.object(setup, "_COPY_PATHS", self.paths).start()
        for relative in self.paths:
            target = self.staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative.suffix:
                target.write_text("staged executable", encoding="utf-8")
            else:
                target.mkdir()
                (target / "owned.txt").write_text("staged package", encoding="utf-8")

    def _assert_fully_staged(self) -> None:
        for relative in self.paths:
            self.assertTrue((self.staging / relative).exists())
            self.assertFalse((self.cache / relative).exists())

    def test_late_move_failure_restores_prior_moves(self) -> None:
        """A later rename failure leaves no partial install that blocks a retry."""
        rename = Path.rename

        def _failing_rename(source: Path, destination: Path) -> Path:
            if source == self.staging / self.paths[2]:
                raise OSError("simulated publication failure")
            return rename(source, destination)

        with mock.patch.object(Path, "rename", _failing_rename), \
                mock.patch.object(setup, "_check_runtime") as check, \
                self.assertRaisesRegex(OSError, "simulated publication failure"):
            setup._publish_runtime(self.staging, self.cache)
        self._assert_fully_staged()
        check.assert_not_called()
        with mock.patch.object(setup, "_check_runtime"):
            setup._publish_runtime(self.staging, self.cache)
        for relative in self.paths:
            self.assertTrue((self.cache / relative).exists())

    def test_final_validation_failure_restores_all_moves(self) -> None:
        """Runtime import errors at the final paths roll back the complete install."""
        with mock.patch.object(setup, "_check_runtime", side_effect=RuntimeError("bad runtime")), \
                self.assertRaisesRegex(RuntimeError, "bad runtime"):
            setup._publish_runtime(self.staging, self.cache)
        self._assert_fully_staged()

    def test_new_destination_conflict_preserves_unowned_content(self) -> None:
        """A destination created after preflight survives while earlier moves revert."""
        conflicting = self.cache / self.paths[1]
        conflicting.parent.mkdir(parents=True)
        conflicting.write_text("other process data", encoding="utf-8")
        with mock.patch.object(setup, "_check_runtime"), \
                self.assertRaisesRegex(RuntimeError, "refusing overwrite"):
            setup._publish_runtime(self.staging, self.cache)
        self.assertEqual(conflicting.read_text(encoding="utf-8"), "other process data")
        self.assertTrue((self.staging / self.paths[0]).is_dir())
        self.assertFalse((self.cache / self.paths[0]).exists())

    def test_replacement_is_preserved_and_recovery_is_reported(self) -> None:
        """Rollback preserves a replacement directory that belongs to another process."""
        destination = self.cache / self.paths[0]
        original_elsewhere = self.cache / "moved-by-other-process"

        def _replace_before_failure(cache: Path) -> None:
            destination.rename(original_elsewhere)
            destination.mkdir()
            (destination / "private.txt").write_text("preserve me", encoding="utf-8")
            raise RuntimeError("bad runtime")

        with mock.patch.object(setup, "_check_runtime", side_effect=_replace_before_failure), \
                self.assertRaisesRegex(setup._RuntimeRollbackError, "retained at"):
            setup._publish_runtime(self.staging, self.cache)
        self.assertEqual((destination / "private.txt").read_text(encoding="utf-8"), "preserve me")
        self.assertTrue((original_elsewhere / "owned.txt").is_file())
        self.assertTrue((self.staging / self.paths[1]).is_file())
        self.assertTrue((self.staging / self.paths[2]).is_dir())

    def test_keyboard_interrupt_also_restores_prior_moves(self) -> None:
        """Ctrl+C during final validation follows the same rollback path as errors."""
        with mock.patch.object(setup, "_check_runtime", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            setup._publish_runtime(self.staging, self.cache)
        self._assert_fully_staged()

    def test_interrupt_after_native_rename_still_restores_asset(self) -> None:
        """An interrupt delivered just after the OS move cannot leave untracked files."""
        rename = Path.rename

        def _interrupted_rename(source: Path, destination: Path) -> Path:
            result = rename(source, destination)
            if source == self.staging / self.paths[0]:
                raise KeyboardInterrupt
            return result

        with mock.patch.object(Path, "rename", _interrupted_rename), \
                mock.patch.object(setup, "_check_runtime"), \
                self.assertRaises(KeyboardInterrupt):
            setup._publish_runtime(self.staging, self.cache)
        self._assert_fully_staged()


if __name__ == "__main__":
    unittest.main()
