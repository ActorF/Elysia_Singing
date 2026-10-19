# Bundled assets and licensing

[简体中文](ASSETS.md) | **English** · [Project overview](README.en.md)

This project creates AI song covers through singing voice conversion, using Elysia's Chinese-dub voice from *Honkai Impact 3rd* as the voice reference. Outputs are AI-generated audio and do not imply participation by the original voice actor or official endorsement.

Original project source code is licensed under the [MIT License](LICENSE), copyright (c) 2026 Ying Fang. That license does not grant rights to third-party software, models, reference audio, or character assets; their existing terms and rights remain separate.

## Release contents

At the project owner's request, the GitHub Release includes the models, Elysia singing reference, and runtime dependencies needed by this fixed conversion workflow. Users' input recordings and generated songs remain local and are not release assets.

The Linux bundle is built from a clean Ubuntu Base image. It contains a dedicated `elysia` user, the reviewed Python/inference environment, public SoulX source, selected models, and the two Elysia reference assets. It excludes personal shell history, credentials, unrelated prompt banks, other voice references, input songs, and previous inference jobs.

| Asset | Version or identity |
| --- | --- |
| SoulX-Singer source | Revision `81aeb3ae772c70093c3de74dc23c92d983801ae4` |
| SoulX SVC model | `model-svc.pt`, SHA-256 `38e72d9e15be6eef3cc937daaf81421a7b5b546816786530bb9aa346cee5d43c` |
| Whisper content encoder | `openai/whisper-base`, weights SHA-256 `07cadb9f25677c8d50df603e66a98fbd842cce45047139baeb16e6219a1e807b` |
| RMVPE pitch model | SHA-256 `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193` |
| Elysia singing reference | `elysia.wav`, SHA-256 `46356e5b54e11624449a922952ea4823f372094fc10f07f7048943cc2e794d7a` |
| Elysia reference pitch | `elysia.npy`, SHA-256 `06d1d9a5fc6653116458b546b7432372b039d463d29ac227624f24a78269bfce` |
| Source separation | Demucs 4.0.1, `htdemucs` checkpoint |
| Windows tools | Embedded Python, CUDA Torch, FFmpeg/FFprobe and audio dependencies |

`scripts/song_svc_assets.py` and the native audio worker enforce exact model lengths and identities. `release-manifest.json` records the byte length and SHA-256 of each compressed runtime part.

The Release also includes `windows-runtime.manifest.json` with package inventories and `ffmpeg-corresponding-sources.zip` with the exact upstream FFmpeg source snapshots, COPYING files, and recorded binary build configurations. The accompanying notes explain the limits of the available original vendor build materials.

## Third-party rights

Bundled software retains its upstream license and notice files. This project does not relicense third-party content or claim ownership of third-party models, recordings, or characters.

Elysia is a HoYoverse / miHoYo character, and this project is unofficial. Reference assets are bundled at the project owner's request; their inclusion does not grant third-party character, recording, performance, or commercial-use rights.
