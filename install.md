---
name: video-use-install
description: Install video-use into the current agent and wire up ffmpeg + iFlytek long-form ASR credentials.
---

# video-use 安装说明

这个文件只用于首次安装或重新连接。日常剪辑请读 `SKILL.md`。每次实际工作前都要读 `helpers/`，因为转写、打包、渲染、调色等脚本都在那里。

## 安装目标

你要为用户配置一个对话式视频剪辑工作流。安装完成后，用户只需要把原始素材放到任意文件夹，在那里启动 agent（`claude`、`codex` 等），然后说“把这些素材剪成一个视频”。后续剪辑流程按 `SKILL.md` 执行。

本机需要具备三样东西：

1. `video-use` 仓库被克隆到一个稳定路径。
2. `ffmpeg` 和 `ffprobe` 在 `$PATH` 上；`yt-dlp` 可选，用于下载在线视频。
3. 仓库根目录的 `.env` 里有科大讯飞语音转写凭证。

当前 agent 还必须能发现这个 skill：

4. 它可以通过全局 skills 目录（如 `~/.claude/skills/`、`~/.codex/skills/`）发现 `SKILL.md`，或通过系统提示词 / `CLAUDE.md` 等方式导入。

## 安装约定

- 尽量自己完成安装。只在需要用户提供无法生成的信息时提问，比如科大讯飞 `APP_ID` / `SECRET_KEY`，以及安装系统依赖前的确认。
- 优先使用稳定路径，例如 `~/Developer/video-use`，不要放在 `/tmp` 或 `~/Downloads`。
- 注册 skill 时要 symlink **整个目录**，不能只链接 `SKILL.md`。`SKILL.md` 和 `helpers/` 必须保持相邻。
- 安装后至少运行一个真实命令验证，不要只检查文件是否存在。

## 步骤

### 1. 克隆到稳定路径

```bash
test -d ~/Developer/video-use || git clone git@github.com:VanGoghBuilder/video-use.git ~/Developer/video-use
cd ~/Developer/video-use
```

如果仓库已经存在，执行 `git pull --ff-only` 后继续。

### 2. 安装 Python 依赖

```bash
# 优先使用 uv；没有 uv 时退回 pip。
command -v uv >/dev/null && uv sync || pip install -e .
```

`pyproject.toml` 声明了 `requests`、`librosa`、`matplotlib`、`pillow`、`numpy` 等依赖。项目没有安装 console script，helper 直接用 `python helpers/<name>.py` 调用。

### 3. 安装 ffmpeg 和可选 yt-dlp

`ffmpeg` 和 `ffprobe` 是必需项。`yt-dlp` 只在用户需要从 URL 下载素材时才需要。

```bash
# macOS
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v yt-dlp >/dev/null || brew install yt-dlp     # 可选

# Debian / Ubuntu
# sudo apt-get update && sudo apt-get install -y ffmpeg
# pip install yt-dlp

# Arch
# sudo pacman -S ffmpeg yt-dlp
```

如果 `brew`、`apt` 或 `pacman` 需要用户输入密码，把准确命令告诉用户并等待。不要猜密码。

### 4. 注册到当前 agent

先判断当前运行在哪个 agent 下，然后注册一次。正确做法是 symlink 整个仓库目录，因为 `helpers/` 必须和 `SKILL.md` 在一起。

- **Claude Code**（存在 `~/.claude/`）：

    ```bash
    mkdir -p ~/.claude/skills
    ln -sfn ~/Developer/video-use ~/.claude/skills/video-use
    ```

- **Codex**（设置了 `$CODEX_HOME`，或存在 `~/.codex/`）：

    ```bash
    mkdir -p "${CODEX_HOME:-$HOME/.codex}/skills"
    ln -sfn ~/Developer/video-use "${CODEX_HOME:-$HOME/.codex}/skills/video-use"
    ```

- **Hermes / Openclaw / 其他有 skills 目录的 agent**：把 `~/Developer/video-use` symlink 到对应 skills 目录，名字用 `video-use`。如果没有 skills 目录，就在系统提示词或配置里指向 `~/Developer/video-use/SKILL.md`。

如果无法判断当前 agent，就问用户一次：“我现在运行在哪个 agent 下，是 Claude Code、Codex，还是别的？”然后选择对应注册方式。

### 5. 配置科大讯飞语音转写凭证

这个分支使用科大讯飞语音转写做所有转写。没有凭证就不能转写。

1. 按下面顺序检查已有状态，命中任意一种即可停止：

    ```bash
    # a) 环境变量已存在
    [ -n "$XFYUN_APP_ID" ] && [ -n "$XFYUN_SECRET_KEY" ] && echo "env"
    # b) 仓库根目录 .env 已存在
    grep -q '^XFYUN_APP_ID=..' ~/Developer/video-use/.env 2>/dev/null && \
      grep -q '^XFYUN_SECRET_KEY=..' ~/Developer/video-use/.env 2>/dev/null && echo "dotenv"
    ```

2. 如果都没有，向用户索要一次：

    > 我需要你的科大讯飞语音转写凭证：已开通“语音转写”的讯飞开放平台应用里的 `APP_ID` 和 `SECRET_KEY`。请把两项都发给我，我会写入 `~/Developer/video-use/.env`。如果你已经导出了 `XFYUN_APP_ID` 和 `XFYUN_SECRET_KEY`，回复 “use env” 即可。

    用户提供后，写入 `~/Developer/video-use/.env`：

    ```bash
    printf 'XFYUN_APP_ID=%s\nXFYUN_SECRET_KEY=%s\n' "$APP_ID" "$SECRET_KEY" > ~/Developer/video-use/.env
    chmod 600 ~/Developer/video-use/.env
    ```

    不要把 secret 回显到输出里。不要提交 `.env`。

3. 做不消耗转写额度的 sanity check：

    ```bash
    python ~/Developer/video-use/helpers/transcribe.py --help >/dev/null && echo "xfyun helper OK"
    ```

    完整凭证验证需要创建真实转写任务，会消耗额度，所以等用户提供第一个素材后再做。

### 6. 验证安装

运行一个足够轻量但真实的命令，确认链路可用：

```bash
python ~/Developer/video-use/helpers/timeline_view.py --help >/dev/null && echo "helpers OK"
ffprobe -version | head -1
```

安装阶段不要默认跑完整转写测试，因为会消耗讯飞额度。等用户给素材后再跑。

### 7. 交付说明

用一条简短消息告诉用户：

- skill 安装在哪里，例如 `~/Developer/video-use`。
- 用户应该进入素材目录启动 agent，例如 `cd /path/to/videos && claude`。
- 推荐第一句话可以是：“把这些素材剪成一个发布视频”，或“先盘点这些素材并给出剪辑策略”。
- 所有输出都会写到 `<videos_dir>/edit/`，仓库目录保持干净。

## 更新 skill

- `cd ~/Developer/video-use && git pull --ff-only` 拉取最新代码。symlink 会在下次运行时自动使用新内容。
- 如果 `pyproject.toml` 的依赖变了，拉取后重新执行 `uv sync` 或 `pip install -e .`。

## 冷启动提醒

- symlink **整个目录**，不要只链接 `SKILL.md`。
- 如果 `.env` 存在但任意凭证为空，当作缺失处理。
- 静态构建版 `ffmpeg` 也可以，现代版本（≥ 4.x）即可。
- `yt-dlp` 是可选项，用户第一次需要从 URL 拉素材时再装也行。
- 不要在安装验证时默认跑转写，除非用户明确要求，因为 ASR 会产生费用。
- 如果 Linux 环境没有可识别的包管理器，给出手动安装 `ffmpeg` 的链接或命令并等待用户处理。
