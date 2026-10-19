# 素材与授权

**简体中文** | [English](ASSETS.en.md) · [项目介绍](README.md)

本项目使用《崩坏3》中爱莉希雅（Elysia）的中文配音作为音色参考，通过 AI 歌声音色转换制作翻唱。输出为 AI 生成音频，不代表原配音演员参与录制或官方认可。

项目原创源代码采用 [MIT 许可证](LICENSE)，版权所有 © 2026 Ying Fang。该许可证不授予第三方软件、模型、参考音频或角色素材的权利；这些内容仍受各自的条款与权利约束。

## 发布包内容

GitHub Release 按项目所有者的要求，包含此固定转换流程所需的模型、爱莉希雅歌声参考和运行依赖。用户输入的录音和生成的歌曲保留在本地，不属于发布包素材。

Linux 运行包基于干净的 Ubuntu Base 镜像构建，包含专用 `elysia` 用户、经审查的 Python／推理环境、公开的 SoulX 源码、指定模型及两份爱莉希雅参考素材。不包含个人 Shell 历史、凭据、无关提示词库、其他声音参考、输入歌曲或历史推理任务。

| 素材 | 版本或标识 |
| --- | --- |
| SoulX-Singer 源码 | 提交 `81aeb3ae772c70093c3de74dc23c92d983801ae4` |
| SoulX SVC 模型 | `model-svc.pt`，SHA-256 `38e72d9e15be6eef3cc937daaf81421a7b5b546816786530bb9aa346cee5d43c` |
| Whisper 内容编码器 | `openai/whisper-base`，权重 SHA-256 `07cadb9f25677c8d50df603e66a98fbd842cce45047139baeb16e6219a1e807b` |
| RMVPE 音高模型 | SHA-256 `6d62215f4306e3ca278246188607209f09af3dc77ed4232efdd069798c4ec193` |
| 爱莉希雅歌声参考 | `elysia.wav`，SHA-256 `46356e5b54e11624449a922952ea4823f372094fc10f07f7048943cc2e794d7a` |
| 爱莉希雅参考音高 | `elysia.npy`，SHA-256 `06d1d9a5fc6653116458b546b7432372b039d463d29ac227624f24a78269bfce` |
| 音源分离 | Demucs 4.0.1，`htdemucs` 检查点 |
| Windows 工具 | 嵌入式 Python、CUDA Torch、FFmpeg／FFprobe 及音频依赖 |

`scripts/song_svc_assets.py` 和原生音频工作进程会校验模型的精确长度及标识。`release-manifest.json` 记录各运行包压缩分卷的字节长度和 SHA-256。

发布包还包含 `windows-runtime.manifest.json`（软件包清单）及 `ffmpeg-corresponding-sources.zip`（对应版本的 FFmpeg 上游源码快照、COPYING 文件和二进制构建配置记录）。随附说明记录了可获得的原始供应商构建材料的局限。

## 第三方权利

随包软件保留上游许可证和声明文件。本项目不会为第三方内容重新授权，也不主张拥有第三方模型、录音或角色。

爱莉希雅是 HoYoverse／miHoYo 旗下角色，本项目为非官方项目。参考素材按项目所有者的要求随包提供，并不因此授予第三方角色、录音、表演或商业使用权利。
