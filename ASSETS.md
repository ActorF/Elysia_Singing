# Bundled assets and source provenance

The audio pipeline was extracted from ActorF/Elysia_AI. At the project owner's explicit request, the GitHub Release includes the models, Elysia singing reference, and runtime dependencies needed by this fixed conversion workflow. Generated songs and users' input recordings remain local and are not release assets.

The Linux bundle is built from a clean Ubuntu Base image, rather than exported from a developer's personal distribution. It contains a dedicated `elysia` user, the reviewed Python/inference environment, public SoulX source, selected model files, and the two Elysia reference assets. It excludes personal shell history, credentials, unrelated prompt banks, other voice references, input songs, and previous inference jobs.

| Asset | Selection |
| --- | --- |
| SoulX-Singer source | Revision `81aeb3ae772c70093c3de74dc23c92d983801ae4` |
| SoulX SVC model | `model-svc.pt`, SHA-256 `38e72d9e15be6eef3cc937daaf81421a7b5b546816786530bb9aa346cee5d43c` |
| Whisper content encoder | `openai/whisper-base`, weights SHA-256 `07cadb9f25677c8d50df603e66a98fbd842cce45047139baeb16e6219a1e807b` |
| RMVPE pitch model | SHA-256 `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193` |
| Elysia singing reference | `elysia.wav`, SHA-256 `46356e5b54e11624449a922952ea4823f372094fc10f07f7048943cc2e794d7a` |
| Elysia reference pitch | `elysia.npy`, SHA-256 `06d1d9a5fc6653116458b546b7432372b039d463d29ac227624f24a78269bfce` |
| Source separation | Demucs 4.0.1, `htdemucs` checkpoint |
| Windows tools | Embedded Python, CUDA Torch, FFmpeg/FFprobe and audio dependencies |

Exact model lengths and identities remain enforced in `scripts/song_svc_assets.py` and the native audio worker. `release-manifest.json` identifies every compressed runtime part by byte length and SHA-256. Existing upstream license and notice files are preserved with the bundled software; this repository does not relicense them or claim ownership of third-party models, recordings, or characters.

The Release also includes `windows-runtime.manifest.json` with package inventories and `ffmpeg-corresponding-sources.zip` with the exact upstream FFmpeg source snapshots, COPYING files and recorded binary build configurations. The sidecar explains the limits of the available original vendor build materials.

Elysia is associated with HoYoverse / miHoYo. This project is unofficial. The supplied reference is included at the project owner's request; that inclusion is not a grant of third-party character, recording, performance, or commercial-use rights.
