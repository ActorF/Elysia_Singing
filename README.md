# Elysia Singing

独立的 Windows CMD 翻唱程序，固定使用 **Voice conversion → Elysia → Entire vocal track → Complete song**。发行包包含所需模型、Elysia 歌唱参考、Windows 音频依赖和专用 Linux 推理环境，不需要安装 Elysia AI。

## 下载与启动

1. 打开 [Releases](https://github.com/ActorF/Elysia_Singing/releases/latest)，下载 `Elysia-Singing-Windows.zip` 并解压到固定文件夹。
2. 双击 `start.cmd`。首次启动自动下载、校验并部署该版本的运行包；后续启动直接使用本地环境。
3. 按顺序输入歌曲路径、SongKey、伴奏是否跟随变调。

```text
Complete song path (or drag file here): "D:\Music\song.wav"
SongKey (-12 to 12): 0
Shift accompaniment with vocal (yes/no): yes
```

**系统要求：Windows x64、支持 CUDA 的 NVIDIA 显卡及驱动、WSL2。** 首次部署需要联网和足够磁盘空间保存压缩包及展开后的环境。模型为数 GB，首次下载和部署需要时间。没有 WSL2 时，启动器会显示安装命令；以管理员身份执行 `wsl --install --no-distribution`，按 Windows 提示完成安装/重启后重新打开 `start.cmd`。本程序不会安装 Linux 显卡驱动或修改用户已有发行版。

程序创建独立的 `Elysia-Singing-<项目编号>` WSL 发行版，存储于本项目的 `models/wsl`。不使用电脑默认的 Linux 发行版，也不需要用户手动配置 Python、pip、模型目录或参考音频。首次安装后请保持项目目录位置不变。

如需预先下载全部内容，可把同一 Release 的所有 `.partNN` 文件放进一个目录，然后执行：

```bat
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 -BundleDirectory "D:\Downloads\Elysia-runtime"
start.cmd
```

源码页面的 “Download ZIP” 不含二进制模型，但其 `start.cmd` 同样能根据固定版本清单自动下载运行包。发行资产按大小分卷，启动器会自动拼接；请勿直接解压单个 `.partNN` 文件。

## 使用

- **歌曲路径**：完整歌曲，支持拖进 CMD，支持中文、空格和双引号。
- **SongKey**：-12 到 12 的整数半音，0 保持原调。非法输入会重新询问。
- **伴奏跟随**：`yes` / `y` 让伴奏与人声一起移调；`no` / `n` 保留伴奏原调。

也可以在 CMD 一次传入参数：

```bat
start.cmd "D:\Music\song.wav" --song-key -2 --shift-accompaniment yes
```

输入支持 AAC、FLAC、M4A、MP3、OGG、OPUS、WAV，文件不超过 1 GiB，时长 1–720 秒。转换覆盖整条人声，包括和声与重叠歌声，不会自动辨认不同歌手。人声与伴奏使用不同调性时可能不和谐。

## 输出

结果始终保存在**本项目工作目录**的 `output`，与 CMD 的当前目录无关：

```text
Elysia_Singing/
├── start.cmd
├── output/
│   └── <任务 UUID>/
│       ├── elysia-cover.wav       # 完整歌曲：44.1 kHz stereo PCM16
│       ├── elysia-cover.mp3       # 完整歌曲：44.1 kHz、320 kbps
│       └── generated_vocal.wav    # 完整转换人声：24 kHz mono PCM24
├── workspace/
│   ├── jobs/                     # 未完成任务的中间文件
│   ├── logs/                     # 本地诊断日志
│   └── profiles/                 # 隔离的 Windows 缓存
└── models/                       # 自动部署的本项目运行环境
```

每次生成使用独立 UUID，不覆盖旧结果，也不会在退出时删除成品。输入歌曲、运行日志和生成结果不上传。推理阶段离线运行。

## 取消、恢复与检查

按 **Ctrl+C** 取消。程序先停止 Windows 工作进程树，再清理相应 UUID 的 Linux 任务；清理失败会保留恢复标记。直接关闭窗口或电脑重启后，下次启动会检查未完成任务。

```bat
start.cmd --recover
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 -CheckOnly
```

上一工作进程仍在运行时，不会删除它的数据。失败任务的本地中间文件和缓存保留在 `workspace`，确认没有运行任务后可自行清理。最终音频不受恢复操作影响。

## 开发与打包

源码检查使用 Python 3.12+，不需要模型或 GPU：

```bat
python scripts/check_python_documentation.py
python -m unittest discover -s tests -v
```

发布工具在 `scripts/build_windows_bundle.py`、`scripts/build_linux_bundle.py` 和 `scripts/package_release.py`。运行资产保存在 GitHub Releases，Git 只跟踪源码、文档和包含长度/SHA-256 的固定版本清单。`setup.cmd --from-project <路径>` 是维护者从旧 Elysia AI 导入 Windows 依赖的辅助工具，普通使用者无需运行。

音频处理代码提取自 ActorF/Elysia_AI，基线提交 `5b2fae1787611f9cc9288dce8bc1f3a9c7a9dd58`。保留分段、时间对齐、音高修正、混音和任务清理；独立入口只开放上述固定组合。模型及参考资产见 [ASSETS.md](ASSETS.md)。
