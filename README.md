<p align="center">
  <img src="static/video-use-banner.png" alt="video-use" width="100%">
</p>

# video-use

**video-use** 是一个面向智能体的视频剪辑工作流。把原始素材放进一个文件夹，和 Claude Code、Codex 或其他支持 shell 的 agent 对话，就可以得到剪辑后的 `final.mp4`。

它适合口播、访谈、教程、旅行记录、活动花絮、产品发布等素材。这个分支已经把转写服务从 ElevenLabs 替换为**科大讯飞语音转写**。

## 能做什么

- **按逐词时间戳剪辑**，避免切在词中间
- **删除废话、重复、明显停顿和无效片段**
- **自动或手动调色**，支持 `warm_cinematic`、`neutral_punch` 或任意 ffmpeg filter
- **每个剪点加 30ms 音频淡入淡出**，减少爆音
- **生成并烧录字幕**，默认是短块大字字幕，也可以自定义
- **生成动画叠层**，可使用 Manim、Remotion 或 PIL
- **在交付前自检输出**，检查剪点、字幕遮挡、动画错位和音画问题
- **把项目记忆写入 `project.md`**，下次继续剪时可以接上上下文

## 一次性安装 Prompt

把下面这段发给 Claude Code、Codex、Hermes、Openclaw，或任何能访问 shell 的 agent：

```text
Set up https://github.com/browser-use/video-use for me.

Read install.md first to install this repo, wire up ffmpeg, register the skill with whichever agent you're running under, and set up the iFlytek long-form ASR credentials — ask me to paste them when you need them. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own — just tell me it's ready and wait for me to drop footage into a folder.
```

agent 会完成 clone、依赖安装、skill 注册，并在需要时向你索要科大讯飞的 `APP_ID` 和 `SECRET_KEY`。

安装完成后，在素材目录里启动你的 agent：

```bash
cd /path/to/your/videos
claude    # 或 codex、hermes 等
```

然后在会话里说：

> 把这些素材剪成一个发布视频

agent 会先盘点素材、提出剪辑策略，等你确认后再生成 `edit/final.mp4`。所有输出都会落在 `<videos_dir>/edit/`，不会写进 `video-use` 项目目录。

## 手动安装

如果你想手动配置：

```bash
# 1. 克隆仓库，并把整个目录注册到 agent 的 skills 目录
git clone git@github.com:VanGoghBuilder/video-use.git ~/Developer/video-use
ln -sfn ~/Developer/video-use ~/.claude/skills/video-use        # Claude Code
# ln -sfn ~/Developer/video-use ~/.codex/skills/video-use       # Codex

# 2. 安装依赖
cd ~/Developer/video-use
uv sync                         # 或 pip install -e .
brew install ffmpeg             # 必需
brew install yt-dlp             # 可选，用于下载在线视频源

# 3. 配置科大讯飞语音转写凭证
cp .env.example .env
$EDITOR .env                    # XFYUN_APP_ID=..., XFYUN_SECRET_KEY=...
```

`.env` 示例：

```bash
XFYUN_APP_ID=你的讯飞应用 APPID
XFYUN_SECRET_KEY=你的讯飞语音转写 SecretKey
```

## 工作原理

LLM 不会逐帧“观看”整条视频。它主要通过两层信息来“阅读”素材，从而用词边界精度做剪辑。

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view composite — filmstrip + speaker track + waveform + word labels + silence-gap cut candidates" width="100%">
</p>

**第一层：音频转写。** 每个源素材调用一次科大讯飞语音转写，得到逐词时间戳和说话人信息。所有素材会被压缩成一个 `takes_packed.md`，这是 LLM 选择剪点的主要阅读材料。

```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

**第二层：按需视觉检查。** `timeline_view.py` 可以为任意时间段生成胶片条、波形和词标签图。它只在关键决策点使用，比如判断停顿、比较重录片段、检查剪点。

> 朴素做法：30,000 帧 × 1,500 tokens = **4,500 万 tokens 的噪声**。  
> video-use：**十几 KB 的文本 + 少量关键 PNG**。

这和 browser-use 给 LLM 结构化 DOM 而不是整页截图类似，只是这里的对象换成了视频。

## 流程

```
转写 ──> 打包 transcript ──> LLM 选择剪辑策略 ──> EDL ──> 渲染 ──> 自检
                                                                       │
                                                                       └─ 有问题则修复并重渲染，最多 3 轮
```

自检阶段会在渲染后的成片上检查剪点附近的画面、波形、字幕和叠层，尽量在展示给你之前发现跳切、爆音、字幕被遮挡等问题。

## 设计原则

1. **文本为主，视觉按需。** 不批量抽帧，不把视频变成 token 垃圾。
2. **音频优先，画面跟进。** 剪点先来自词边界和静音，再用视觉检查确认。
3. **先沟通，再执行。** 正常工作流里，先提出策略，确认后再剪。
4. **不预设内容类型。** 先看素材，再决定剪法。
5. **正确性规则不可破坏，审美可以自由发挥。**

完整执行规则和剪辑工艺见 [`SKILL.md`](./SKILL.md)。
