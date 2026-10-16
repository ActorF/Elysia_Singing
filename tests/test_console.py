"""Verify console choices, project-local results and interrupted-job recovery.

Tests replace the private audio runtime with controlled subprocess boundaries;
they exercise the console transaction and progress protocol without models,
WSL, CUDA, downloads or a user's recordings.
"""

from __future__ import annotations

from contextlib import ExitStack, redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock
import uuid

import main as console


class ConsoleTests(unittest.TestCase):
    """Cover user choices and the filesystem/process ownership of a song job."""

    def setUp(self) -> None:
        """Create an isolated project so no test can alter the real Workspace."""
        temporary = tempfile.TemporaryDirectory(prefix="elysia-console-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.song = self.root / "a song with spaces.wav"
        self.song.write_bytes(b"mock audio")
        self.paths = console._paths(self.root)
        self.output = io.StringIO()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(console, "PROJECT_ROOT", self.root).start()

    def _runtime_patches(self, worker_effect=None) -> ExitStack:
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(console, "_require_runtime", return_value=self.paths))
        self.process = mock.Mock(pid=123456, stdout=io.BytesIO(), returncode=0)
        self.process.poll.return_value = 0
        self.process.wait.return_value = 0
        self.spawn = stack.enter_context(mock.patch.object(console.subprocess, "Popen", return_value=self.process))
        self.stream = stack.enter_context(mock.patch.object(console, "_stream_worker", side_effect=worker_effect))
        self.stop = stack.enter_context(mock.patch.object(console, "_stop_process"))
        self.cleanup = stack.enter_context(mock.patch.object(console, "_cleanup"))
        stack.enter_context(redirect_stdout(self.output))
        return stack

    def test_song_key_accepts_inclusive_integer_boundaries(self) -> None:
        """The allowed pitch range includes both endpoints and explicit signs."""
        for value, expected in (("-12", -12), ("12", 12), ("+7", 7), (" 0 ", 0)):
            with self.subTest(value=value):
                self.assertEqual(console.parse_song_key(value), expected)

    def test_song_key_rejects_out_of_range_and_nonintegers(self) -> None:
        """Invalid console input cannot silently round, clip or select a default."""
        for value in ("-13", "13", "1.5", "", "zero", "1e1", "--1", "１２"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                console.parse_song_key(value)

    def test_accompaniment_requires_explicit_yes_or_no(self) -> None:
        """Both full answers and abbreviations work without truthy-string bugs."""
        for value, expected in (("YES", True), ("y", True), (" no ", False), ("N", False)):
            with self.subTest(value=value):
                self.assertIs(console.parse_yes_no(value), expected)
        for value in ("", "true", "false", "1", "maybe"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                console.parse_yes_no(value)

    def test_interactive_prompts_retry_and_preserve_chosen_values(self) -> None:
        """CMD asks song, key and accompaniment, retrying invalid answers in place."""
        destination = self.root / "output" / "example"
        answers = [f'"{self.song}"', "13", "-3", "maybe", "no"]
        with mock.patch("builtins.input", side_effect=answers) as prompt, \
                mock.patch.object(console, "run_conversion", return_value=destination) as run, \
                redirect_stdout(self.output):
            self.assertEqual(console.main([]), 0)
        self.assertIn("Complete song path", prompt.call_args_list[0].args[0])
        self.assertIn("SongKey", prompt.call_args_list[1].args[0])
        self.assertIn("SongKey", prompt.call_args_list[2].args[0])
        self.assertIn("Shift accompaniment with vocal", prompt.call_args_list[3].args[0])
        self.assertIn("Shift accompaniment with vocal", prompt.call_args_list[4].args[0])
        run.assert_called_once_with(self.root, self.song, -3, False)
        self.assertIn("Entire vocal track | Complete song", self.output.getvalue())
        self.assertIn(str(destination), self.output.getvalue())

    def test_explicit_zero_and_no_do_not_prompt_again(self) -> None:
        """False and zero are actual choices, rather than missing flag values."""
        with mock.patch("builtins.input", side_effect=AssertionError("unexpected prompt")), \
                mock.patch.object(console, "run_conversion", return_value=self.root) as run, \
                redirect_stdout(self.output):
            status = console.main([str(self.song), "--song-key", "0", "--shift-accompaniment", "no"])
        self.assertEqual(status, 0)
        run.assert_called_once_with(self.root, self.song, 0, False)

    def test_prompt_cancellation_returns_conventional_exit_code(self) -> None:
        """Both Ctrl+C and closed stdin exit without starting the audio runtime."""
        for interrupt in (KeyboardInterrupt, EOFError):
            with self.subTest(interrupt=interrupt), \
                    mock.patch("builtins.input", side_effect=interrupt), \
                    mock.patch.object(console, "run_conversion") as run, \
                    redirect_stdout(self.output), redirect_stderr(self.output):
                self.assertEqual(console.main([]), 130)
                run.assert_not_called()

    def test_worker_command_locks_the_complete_song_method(self) -> None:
        """Path arguments stay separate while the single supported method is fixed."""
        job = self.root / "workspace" / "jobs" / str(uuid.uuid4())
        command = console.build_worker_command(self.paths, self.song, job, 4, True)
        options = dict(zip(command[3::2], command[4::2]))
        self.assertEqual(options["--engine"], "source-svc")
        self.assertEqual(options["--source-mode"], "song")
        self.assertEqual(options["--input"], str(self.song))
        self.assertEqual(options["--key-shift-semitones"], "4")
        self.assertEqual(options["--shift-accompaniment"], "true")
        self.assertEqual(options["--job-token"], job.name)
        self.assertNotIn("--reference", options)

    def test_worker_command_rejects_truthy_accompaniment_strings(self) -> None:
        """Programmatic callers cannot accidentally shift by passing the string no."""
        with self.assertRaises(ValueError):
            console.build_worker_command(self.paths, self.song, self.root, 0, "no")

    def test_success_moves_only_final_audio_into_project_workspace(self) -> None:
        """A successful job publishes full outputs under this project's Workspace."""
        def _finish(process, log_path):
            command = self.spawn.call_args.args[0]
            job = Path(command[command.index("--job-dir") + 1])
            request = json.loads((job / "singing-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request, {"schema_version": 1, "reference": "elysia", "key_shift_semitones": -5})
            (job / "singing-request.json").unlink()
            for name in console._OUTPUT_NAMES:
                (job / name).write_bytes(b"final audio")
            self.assertTrue(log_path.is_relative_to(self.root / "workspace"))

        self._runtime_patches(_finish)
        destination = console.run_conversion(self.root, self.song, -5, False)
        self.assertEqual(destination.parent, self.root / "output")
        self.assertEqual({path.name for path in destination.iterdir()}, set(console._OUTPUT_NAMES))
        self.assertEqual(list((self.root / "workspace").glob("pending-*.json")), [])
        self.assertEqual(self.spawn.call_args.kwargs["cwd"], self.root)
        self.assertEqual(self.spawn.call_args.kwargs["env"]["HF_HUB_OFFLINE"], "1")
        self.assertTrue(Path(self.spawn.call_args.kwargs["env"]["TEMP"]).is_relative_to(self.root / "workspace"))
        self.cleanup.assert_not_called()

    def test_cancelled_conversion_stops_worker_then_cleans_private_uuid(self) -> None:
        """Cancellation clears its recovery marker only after exact-job cleanup."""
        self._runtime_patches(KeyboardInterrupt)
        order = mock.Mock()
        order.attach_mock(self.stop, "stop")
        order.attach_mock(self.cleanup, "cleanup")
        with self.assertRaises(KeyboardInterrupt):
            console.run_conversion(self.root, self.song, 2, True)
        self.assertEqual([call[0] for call in order.mock_calls], ["stop", "cleanup"])
        self.stop.assert_called_once_with(self.process)
        token = self.cleanup.call_args.args[1]
        self.assertEqual(str(uuid.UUID(token)), token)
        self.assertEqual(list((self.root / "workspace").glob("pending-*.json")), [])

    def test_failed_cleanup_retains_recovery_marker(self) -> None:
        """Interrupted private state remains discoverable after cleanup fails."""
        self._runtime_patches(console.SingingError("worker failed"))
        self.cleanup.side_effect = console.SingingError("cleanup failed")
        with self.assertRaisesRegex(console.SingingError, "cleanup failed"):
            console.run_conversion(self.root, self.song, 0, False)
        markers = list((self.root / "workspace").glob("pending-*.json"))
        self.assertEqual(len(markers), 1)
        record = json.loads(markers[0].read_text(encoding="utf-8"))
        self.assertEqual(record["pid"], self.process.pid)

    def test_stop_failure_preserves_live_job_for_recovery(self) -> None:
        """Cleanup cannot delete private files until native termination is verified."""
        self._runtime_patches(console.SingingError("worker failed"))
        self.stop.side_effect = console.SingingError("worker could not stop")
        with self.assertRaises(console.SingingError):
            console.run_conversion(self.root, self.song, 0, False)
        self.cleanup.assert_not_called()
        self.assertEqual(len(list((self.root / "workspace").glob("pending-*.json"))), 1)

    def test_spawn_failure_still_cleans_and_retires_marker(self) -> None:
        """Failure to launch the native interpreter cannot leave a stale job lock."""
        self._runtime_patches()
        self.spawn.side_effect = OSError("cannot start interpreter")
        with self.assertRaisesRegex(OSError, "cannot start interpreter"):
            console.run_conversion(self.root, self.song, 0, False)
        self.stop.assert_not_called()
        self.cleanup.assert_called_once()
        self.assertEqual(list((self.root / "workspace").glob("pending-*.json")), [])

    def test_recovery_refuses_to_cleanup_a_still_running_worker(self) -> None:
        """Closing CMD must not allow a new session to delete a live worker's data."""
        workspace = self.root / "workspace"
        workspace.mkdir()
        token = str(uuid.uuid4())
        marker = workspace / f"pending-{token}.json"
        marker.write_text(json.dumps({"job_id": token, "pid": 123456}), encoding="utf-8")
        with mock.patch.object(console, "_pid_exists", return_value=True), \
                mock.patch.object(console, "_cleanup") as cleanup, \
                self.assertRaisesRegex(console.SingingError, "still active"):
            console._recover(self.root, self.paths, workspace)
        cleanup.assert_not_called()
        self.assertTrue(marker.is_file())

    def test_recovery_rejects_marker_filename_mismatch(self) -> None:
        """A marker cannot redirect exact-job cleanup to another UUID."""
        workspace = self.root / "workspace"
        workspace.mkdir()
        marker = workspace / f"pending-{uuid.uuid4()}.json"
        marker.write_text(json.dumps({"job_id": str(uuid.uuid4())}), encoding="utf-8")
        with mock.patch.object(console, "_cleanup") as cleanup, \
                self.assertRaisesRegex(console.SingingError, "invalid recovery marker"):
            console._recover(self.root, self.paths, workspace)
        cleanup.assert_not_called()

    def test_stream_requires_both_completion_event_and_zero_exit(self) -> None:
        """A successful exit alone and a completion message alone are insufficient."""
        cases = ((b"normal diagnostics\n", 0),
                 (b'ELYSIA_SONG_COVER {"stage":"complete","progressPercent":100,"message":"done"}\n', 1))
        for stream, status in cases:
            with self.subTest(status=status):
                process = mock.Mock(stdout=io.BytesIO(stream))
                process.wait.return_value = status
                with self.assertRaises(console.SingingError):
                    console._stream_worker(process, self.root / "worker.log")

    def test_stream_saves_diagnostics_and_displays_stage_progress(self) -> None:
        """Progress comes from real worker events while other output stays in a log."""
        stream = (b"native diagnostic\n"
                  b'ELYSIA_SONG_COVER {"stage":"separating","progressPercent":12,"message":"Separating song"}\n'
                  b'ELYSIA_SONG_COVER {"stage":"complete","progressPercent":100,"message":"done"}\n')
        process = mock.Mock(stdout=io.BytesIO(stream))
        process.wait.return_value = 0
        log = self.root / "worker.log"
        with redirect_stdout(self.output):
            console._stream_worker(process, log)
        self.assertEqual(log.read_bytes(), stream)
        self.assertIn("Separating song", self.output.getvalue())
        self.assertNotIn("native diagnostic", self.output.getvalue())

    def test_stream_rejects_oversized_diagnostic_line(self) -> None:
        """A broken worker cannot grow local diagnostics without the console limit."""
        process = mock.Mock(stdout=io.BytesIO(b"x" * 65537 + b"\n"))
        with self.assertRaisesRegex(console.SingingError, "size limit"):
            console._stream_worker(process, self.root / "worker.log")

    def test_stream_malformed_event_has_a_recoverable_error(self) -> None:
        """Malformed protocol data produces a console error instead of a traceback."""
        for payload in (b"[]", b"null", b'{}', b'{"stage":"separating","progressPercent":"12","message":"bad"}'):
            with self.subTest(payload=payload):
                process = mock.Mock(stdout=io.BytesIO(b"ELYSIA_SONG_COVER " + payload + b"\n"))
                process.wait.return_value = 0
                with self.assertRaises(console.SingingError):
                    console._stream_worker(process, self.root / "worker.log")

    def test_cleanup_requires_exact_success_acknowledgement(self) -> None:
        """A zero cleanup exit cannot erase recovery state without its acknowledgement."""
        process = mock.Mock(returncode=0)
        process.communicate.return_value = (b"", b"")
        with mock.patch.object(console.subprocess, "Popen", return_value=process), \
                self.assertRaisesRegex(console.SingingError, "cleanup failed"):
            console._cleanup(self.paths, str(uuid.uuid4()), self.root, {})

    def test_cleanup_timeout_stops_its_helper_and_reports_retained_state(self) -> None:
        """Cleanup has its own process deadline and preserves actionable recovery."""
        process = mock.Mock()
        process.communicate.side_effect = subprocess.TimeoutExpired("cleanup", 60)
        with mock.patch.object(console.subprocess, "Popen", return_value=process), \
                mock.patch.object(console, "_stop_process") as stop, \
                self.assertRaisesRegex(console.SingingError, "retained for recovery"):
            console._cleanup(self.paths, str(uuid.uuid4()), self.root, {})
        stop.assert_called_once_with(process)


if __name__ == "__main__":
    unittest.main()
