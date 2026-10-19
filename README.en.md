# Elysia Singing

[简体中文](README.md) · **English**

A Windows command-line tool for AI song covers using **Elysia's Chinese-dub voice from Honkai Impact 3rd**. It converts the song's vocals and exports the complete cover.

[Download](https://github.com/ActorF/Elysia_Singing/releases/latest) · [MIT License](LICENSE) · [Asset information](ASSETS.en.md)

## Quick start

**Requirements:** Windows x64, a CUDA-capable NVIDIA GPU with drivers, and WSL2. The first setup downloads about **9 GB**; allow **40 GB** of free disk space.

1. Download `Elysia-Singing-Windows.zip` and extract it to a permanent folder. Keep the folder in place after setup.
2. Double-click `start.cmd` and wait for the initial download, verification, and setup. Later launches use the local environment.
3. Enter or drag in a song file, choose the pitch shift and whether to shift the accompaniment, then wait for the result.

Models, reference audio, and the runtime are configured automatically. If WSL2 is missing, expand “Installation” below.

## Usage

| Input | Description |
| --- | --- |
| Song path | Supports dragging in files, Chinese characters, spaces, and double quotes |
| SongKey | An integer from -12 to 12 semitones; `0` keeps the original pitch |
| Shift accompaniment | `yes` / `y` shifts it with the vocals; `no` / `n` keeps its original pitch |

You can also run the command directly in CMD:

```bat
start.cmd "D:\Music\song.wav" --song-key -2 --shift-accompaniment yes
```

Supported formats: AAC, FLAC, M4A, MP3, OGG, OPUS, and WAV. Files must be at most 1 GiB and 1–720 seconds long. Conversion includes harmonies and overlapping vocals without distinguishing singers. Different pitch shifts for vocals and accompaniment may sound out of tune.

## Output

Results are saved to `output/<job UUID>/` inside the project folder. Each run creates a separate folder without overwriting earlier results.

| File | Content |
| --- | --- |
| `elysia-cover.wav` | Complete song · 44.1 kHz stereo PCM16 |
| `elysia-cover.mp3` | Complete song · 44.1 kHz / 320 kbps |
| `generated_vocal.wav` | Converted vocals · 24 kHz mono PCM24 |

Inference runs offline. Songs, logs, and generated files stay on your computer and are not uploaded. Press **Ctrl+C** to cancel a job.

## More information

<details>
<summary>Installation: WSL2, pre-downloaded bundles, and source ZIP</summary>

If WSL2 is not installed, run this command as an administrator. Follow the Windows installation or restart prompts, then reopen `start.cmd`:

```bat
wsl --install --no-distribution
```

The tool creates a dedicated `Elysia-Singing-<project ID>` WSL distribution in the project's `models/wsl` folder. It does not use the default Linux distribution, modify existing distributions, or install Linux GPU drivers.

To pre-download the runtime, place all `.partNN` files from the same Release in one folder, then run:

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 -BundleDirectory "D:\Downloads\Elysia-runtime"
start.cmd
```

The launcher joins the parts automatically; do not extract individual `.partNN` files. GitHub's “Download ZIP” does not include models, but its `start.cmd` can download the runtime using the pinned version manifest.

</details>

<details>
<summary>Cancellation, recovery, and environment checks</summary>

After **Ctrl+C**, the tool stops the Windows worker process tree, then cleans up the corresponding Linux job. Failed cleanup leaves a recovery marker. After closing the window or restarting the computer, the next launch checks for unfinished jobs. You can also run:

```bat
start.cmd --recover
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 -CheckOnly
```

Recovery does not delete active jobs or final audio. `workspace/jobs`, `workspace/logs`, and `workspace/profiles` hold intermediate files, logs, and Windows caches respectively. You can clean them up after confirming that no jobs are running.

</details>

<details>
<summary>Development and packaging</summary>

Source checks require Python 3.12+ but no models or GPU:

```bat
python scripts/check_python_documentation.py
python -m unittest discover -s tests -v
```

Release tools: `scripts/build_windows_bundle.py`, `scripts/build_linux_bundle.py`, and `scripts/package_release.py`. Runtime assets are hosted in GitHub Releases; the pinned version manifest records byte lengths and SHA-256 hashes.

</details>

## License

Original project source code is licensed under the [MIT License](LICENSE), copyright Ying Fang. The project's MIT license does not grant rights to third-party software, models, reference audio, or character assets. See [ASSETS.en.md](ASSETS.en.md) for sources and rights information.
