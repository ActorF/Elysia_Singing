"""Verify the fixed Elysia backend contract without GPU or private model data."""

from contextlib import redirect_stderr, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import song_cover_worker as audio
from scripts import song_svc_assets as assets
from scripts import song_svc_inference as inference
from scripts import song_svs_runtime as runtime
from scripts import song_svs_worker as worker


class BackendContractTests(unittest.TestCase):
    """Keep unsupported methods closed and preserve all requested outputs."""

    def _arguments(self) -> list[str]:
        return [
            "--input", "song.wav", "--shift-accompaniment", "false",
            "--job-dir", "job", "--ffmpeg", "ffmpeg.exe",
            "--ffprobe", "ffprobe.exe", "--demucs-site", "site-packages",
            "--torch-home", "torch", "--job-token", "token",
        ]

    def test_parser_defaults_to_only_complete_song_conversion(self) -> None:
        """The public worker defaults to complete-song source conversion."""
        arguments = worker._parser().parse_args(self._arguments())
        self.assertEqual(arguments.engine, "source-svc")
        self.assertEqual(arguments.source_mode, "song")
        self.assertIsNone(arguments.vocal_input)
        self.assertIsNone(arguments.accompaniment_input)

    def test_parser_rejects_other_methods_and_out_of_range_keys(self) -> None:
        """Legacy, lyrics, stems, and keys beyond one octave fail at parsing."""
        for option in (
            ["--engine", "lyrics-svs"], ["--engine", "legacy-svc"],
            ["--source-mode", "stems"], ["--key-shift-semitones", "-13"],
            ["--key-shift-semitones", "13"], ["--vocal-input", "vocal.wav"],
        ):
            with self.subTest(option=option), redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as error:
                    worker._parser().parse_args(self._arguments() + option)
                self.assertEqual(error.exception.code, 2)

    def test_request_accepts_elysia_at_both_key_limits(self) -> None:
        """Both allowed octave boundaries retain the fixed Elysia reference."""
        for key in (-12, 0, 12):
            request = assets.parse_singing_request(json.dumps({
                "schema_version": 1, "reference": "elysia", "key_shift_semitones": key,
            }).encode())
            self.assertEqual(request.reference, "elysia")
            self.assertEqual(request.key_shift_semitones, key)

    def test_request_rejects_alternate_reference_and_ambiguous_controls(self) -> None:
        """Alternate voices, booleans, duplicate fields, and paths are refused."""
        for payload in (
            b'{"schema_version":1,"reference":"fairy","key_shift_semitones":0}',
            b'{"schema_version":1,"reference":"elysia","key_shift_semitones":true}',
            b'{"schema_version":1,"reference":"elysia","key_shift_semitones":13}',
            b'{"schema_version":1,"reference":"elysia","key_shift_semitones":0,"path":"x"}',
            b'{"schema_version":1,"reference":"elysia","reference":"elysia","key_shift_semitones":0}',
        ):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                assets.parse_singing_request(payload)

    def test_native_request_matches_cli_key_and_fixed_reference(self) -> None:
        """The native worker rejects request tampering before reading audio."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "singing-request.json"
            for reference, key, expected in (("elysia", -12, -12), ("fairy", 0, 0), ("elysia", 1, 0)):
                request.write_text(json.dumps({
                    "schema_version": 1, "reference": reference, "key_shift_semitones": key,
                }), encoding="utf-8")
                with self.subTest(reference=reference, key=key):
                    if reference == "elysia" and key == expected:
                        worker._validate_svc_request(root, expected)
                    else:
                        with self.assertRaises(worker._SvsWorkerFailure):
                            worker._validate_svc_request(root, expected)

    def test_inference_rejects_alternate_reference_before_model_loading(self) -> None:
        """Direct inference cannot bypass the fixed-reference restriction."""
        root = Path.cwd()
        with self.assertRaises(ValueError):
            inference.SvcInferenceRequest(root, root, root / "vocal.wav", root, "fairy", 0)

    def test_only_elysia_reference_assets_are_required(self) -> None:
        """Removing Fairy does not leave an accidental private-asset dependency."""
        reference_names = {name for name, *_ in assets._ASSETS if name.startswith("reference_")}
        self.assertEqual(reference_names, {"reference_elysia", "reference_f0_elysia"})

    def test_cleanup_keeps_full_vocal_and_both_complete_song_formats(self) -> None:
        """Success discards requests and scratch while retaining all exports."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in worker._FINAL_JOB_FILES:
                (root / name).write_bytes(b"complete-output")
            (root / "singing-request.json").write_bytes(b"request")
            scratch = root / "target_vocal.wav"
            scratch.write_bytes(b"private-source")
            worker._cleanup_success(root, scratch_trees=[], scratch_files=[(scratch, "Scratch")])
            self.assertEqual({entry.name for entry in root.iterdir()}, {
                "generated_vocal.wav", "elysia-cover.wav", "elysia-cover.mp3",
            })

    def test_runtime_rejects_lyrics_job_without_importing_removed_modules(self) -> None:
        """A missing singing request cannot dispatch to inherited lyric code."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(runtime, "_runtime_root", return_value=root), patch.object(
                runtime, "_validate_job_root", return_value=(root, "token"),
            ):
                with self.assertRaises(runtime._SvsRuntimeFailure) as error:
                    runtime._run_job(str(root))
                self.assertEqual(error.exception.code, "invalid_request")

    def test_progress_accepts_only_source_conversion_sequence(self) -> None:
        """Full source progress is accepted while lyrics stages are rejected."""
        lines = []
        for stage, progress in (("validating", 4), ("synthesizing", 58), ("synthesizing", 59), ("synthesizing", 88), ("ready", 100)):
            lines.append("ELYSIA_SONG_SVS " + json.dumps({
                "stage": stage, "progressPercent": progress, "message": "Progress",
            }))
        with redirect_stdout(StringIO()):
            result = worker._relay_svs_protocol(BytesIO(("\n".join(lines) + "\n").encode()))
        self.assertTrue(result.ready)
        with self.assertRaises(worker._SvsWorkerFailure):
            worker._relay_svs_protocol(BytesIO(), engine="lyrics-svs")

    def test_audio_helpers_have_no_legacy_entry_point(self) -> None:
        """The extracted audio library exposes no callable RVC application."""
        self.assertFalse(hasattr(audio, "main"))


if __name__ == "__main__":
    unittest.main()
