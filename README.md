# Elysia Singing

**简体中文** · [English](README.en.md)

Windows 命令行 AI 翻唱工具，使用 **《崩坏3》爱莉希雅（Elysia）的中文配音音色** 转换歌曲人声，输出完整翻唱歌曲。

[下载最新版](https://github.com/ActorF/Elysia_Singing/releases/latest) · [MIT 许可证](LICENSE) · [素材说明](ASSETS.md)

## 快速开始

**环境要求：** Windows x64、支持 CUDA 的 NVIDIA 显卡及驱动、WSL2。首次下载约 **9 GB**，建议预留 **40 GB** 磁盘空间。

1. 下载 `Elysia-Singing-Windows.zip`，解压到固定文件夹，安装后请勿移动。
2. 双击 `start.cmd`，等待首次自动下载、校验和部署；后续启动使用本地环境。
3. 输入或拖入歌曲，选择变调值与伴奏是否跟随，等待生成。

模型、参考音频和运行环境会自动配置。未安装 WSL2 时，请展开下方「安装说明」。

## 使用

| 输入项 | 说明 |
| --- | --- |
| 歌曲路径 | 支持拖入文件，以及中文、空格和双引号 |
| SongKey | -12～12 的整数半音；`0` 保持原调 |
| 伴奏跟随 | `yes` / `y` 一起变调；`no` / `n` 保持伴奏原调 |

也可在 CMD 中直接运行：

```bat
start.cmd "D:\Music\song.wav" --song-key -2 --shift-accompaniment yes
```

支持 AAC、FLAC、M4A、MP3、OGG、OPUS、WAV；文件不超过 1 GiB，时长 1–720 秒。转换包含和声与重叠歌声，不区分歌手；人声与伴奏使用不同调性时可能不和谐。

## 输出

结果保存在项目目录的 `output/<任务 UUID>/`，每次生成独立文件夹，不覆盖旧结果。

| 文件 | 内容 |
| --- | --- |
| `elysia-cover.wav` | 完整歌曲 · 44.1 kHz 立体声 PCM16 |
| `elysia-cover.mp3` | 完整歌曲 · 44.1 kHz / 320 kbps |
| `generated_vocal.wav` | 转换后的人声 · 24 kHz 单声道 PCM24 |

推理离线运行，歌曲、日志和生成结果保留在本机，不上传。按 **Ctrl+C** 可取消任务。

## 更多说明

<details>
<summary>安装说明：WSL2、预下载与源码启动</summary>

未安装 WSL2 时，以管理员身份执行以下命令，按 Windows 提示完成安装或重启后，再打开 `start.cmd`：

```bat
wsl --install --no-distribution
```

程序创建独立的 `Elysia-Singing-<项目编号>` WSL 发行版，存储于项目的 `models/wsl`。它不使用默认 Linux 发行版，不修改已有发行版，也不安装 Linux 显卡驱动。

如需预下载，把同一 Release 的所有 `.partNN` 文件放入一个目录，然后运行：

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 -BundleDirectory "D:\Downloads\Elysia-runtime"
start.cmd
```

启动器会自动拼接分卷，请勿直接解压单个 `.partNN` 文件。GitHub 的 “Download ZIP” 不含模型，但其 `start.cmd` 同样支持按固定版本清单自动下载运行包。

</details>

<details>
<summary>取消、恢复与环境检查</summary>

按 **Ctrl+C** 后，程序先停止 Windows 工作进程树，再清理对应的 Linux 任务；清理失败会保留恢复标记。关闭窗口或重启电脑后，下次启动会检查未完成任务。也可手动执行：

```bat
start.cmd --recover
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 -CheckOnly
```

恢复操作不会删除仍在运行的任务或最终音频。`workspace/jobs`、`workspace/logs` 和 `workspace/profiles` 分别存放中间文件、日志和 Windows 缓存；确认无任务运行后可自行清理。

</details>

<details>
<summary>开发与打包</summary>

源码检查使用 Python 3.12+，不需要模型或 GPU：

```bat
python scripts/check_python_documentation.py
python -m unittest discover -s tests -v
```

发布工具：`scripts/build_windows_bundle.py`、`scripts/build_linux_bundle.py`、`scripts/package_release.py`。运行资产存放于 GitHub Releases，固定版本清单记录字节长度和 SHA-256。

</details>

## 许可

本项目原创源代码采用 [MIT License](LICENSE)，版权归 Ying Fang 所有。第三方软件、模型、参考音频及角色相关素材不因本项目的 MIT 许可而获得授权；来源与权利说明见 [ASSETS.md](ASSETS.md)。
