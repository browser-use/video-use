[English](README.md) | **简体中文** | [日本語](README.ja.md)

<p align="center">
  <img src="static/video-use-banner.png" alt="video-use" width="100%">
</p>

# video-use

隆重介绍 **video-use**——使用 Claude Code 编辑视频。100% 开源。

把原始素材放入文件夹，与 Claude Code 对话，就能得到 `final.mp4`。它适用于任何内容——人物口播、蒙太奇、教程、旅行、访谈——无需预设或菜单。

在 [Browser Use Cloud](https://cloud.browser-use.com/v4?utm_campaign=video-use-use-in-cloud&utm_source=github) 中试用 video-use。

## 功能

- **删除口头填充词**（`umm`、`uh`、说到一半重新开始的片段）以及各次拍摄之间的空白
- **自动为每个片段调色**（温暖电影感、中性强烈风格，或任何自定义 ffmpeg 处理链）
- **在每个剪切点应用 30 毫秒音频淡化**，避免听到爆音
- **按你的样式烧录字幕**——默认以两个单词为一组并全部大写，也可完全自定义
- **通过 [HyperFrames](https://github.com/heygen-com/hyperframes)、[Remotion](https://www.remotion.dev/)、[Manim](https://www.manim.community/) 或 PIL 生成动画叠加层**——并行启动子代理，每个动画对应一个子代理
- **在展示任何内容前，于每个剪切边界自行评估渲染结果**
- **将会话记忆保存在 `project.md` 中**，让下周的会话可以从上次停止的位置继续

## 设置提示词

将以下内容粘贴到 Claude Code、Codex、Hermes、Openclaw 或任何能访问 shell 的代理中：

```text
Set up https://github.com/browser-use/video-use for me.

Read install.md first to install this repo, wire up ffmpeg, register the skill with whichever agent you're running under, and set up the ElevenLabs API key — ask me to paste it when you need it. Then read SKILL.md for daily usage, and always read helpers/ because that's where the editing scripts live. After install, don't transcribe anything on your own — just tell me it's ready and wait for me to drop footage into a folder.
```

代理会处理克隆、依赖项和技能注册，并只提示你一次以获取 ElevenLabs API 密钥（可在 [elevenlabs.io/app/settings/api-keys](https://elevenlabs.io/app/settings/api-keys) 获取）。

然后让代理处理包含原始拍摄素材的文件夹：

```bash
cd /path/to/your/videos
claude    # or codex, hermes, etc.
```

如果要通过自己的 VPS 或 Telegram 进行常驻编辑，请通过 [Browser Use Box](https://browser-use.com/bux) 运行代理。[观看 15 秒演示](https://www.tiktok.com/@browser_use/video/7639824093721758989)。

然后在会话中输入：

> 把这些素材剪成一段发布视频

它会清点源素材、提出策略、等待你的确认，然后在源素材旁生成 `edit/final.mp4`。所有输出都位于 `<videos_dir>/edit/` 中——技能目录会保持整洁。

## 手动安装

如果你更愿意手动完成：

```bash
# 1. Clone and symlink into your agent's skills directory
git clone https://github.com/browser-use/video-use ~/Developer/video-use
ln -sfn ~/Developer/video-use ~/.claude/skills/video-use        # Claude Code
# ln -sfn ~/Developer/video-use ~/.codex/skills/video-use       # Codex

# 2. Install deps
cd ~/Developer/video-use
uv sync                         # or: pip install -e .
brew install ffmpeg             # required
brew install yt-dlp             # optional, for downloading online sources

# 3. Add your ElevenLabs API key
cp .env.example .env
$EDITOR .env                    # ELEVENLABS_API_KEY=...
```

## 工作原理

LLM 从不观看视频，而是通过两个信息层来**阅读**视频；两者共同提供以单词边界精确剪辑所需的一切信息。

<p align="center">
  <img src="static/timeline-view.svg" alt="timeline_view 合成视图——胶片条、说话人轨道、波形、单词标签和静音间隙剪切候选点" width="100%">
</p>

**第 1 层——音频转录（始终加载）。** 每个源素材调用一次 ElevenLabs Scribe，即可获得单词级时间戳、说话人分离和音频事件（`(laughter)`、`(applause)`、`(sigh)`）。所有拍摄素材会打包成单个约 12KB 的 `takes_packed.md`，这是 LLM 的主要阅读视图。

```
## C0103  (duration: 43.0s, 8 phrases)
  [002.52-005.36] S0 Ninety percent of what a web agent does is completely wasted.
  [006.08-006.74] S0 We fixed this.
```

**第 2 层——视觉合成图（按需生成）。** `timeline_view` 可为任意时间范围生成由胶片条、波形和单词标签组成的 PNG。它只会在决策点调用——例如有歧义的停顿、重拍片段对比和剪切点合理性检查。

> 朴素方法：30,000 帧 × 1,500 个 token = **4,500 万个 token 的噪声**。
> Video Use：**12KB 文本 + 少量 PNG**。

这与 browser-use 向 LLM 提供结构化 DOM 而非截图的思路相同——只不过应用于视频。

## 流程

```
Transcribe ──> Pack ──> LLM Reasons ──> EDL ──> Render ──> Self-Eval
                                                              │
                                                              └─ issue? fix + re-render (max 3)
```

自评估循环会在每个剪切边界上对_渲染后的输出_运行 `timeline_view`，从而发现画面跳变、音频爆音和被遮挡的字幕。只有通过检查后，你才会看到预览。

## 设计原则

1. **文本 + 按需视觉信息。** 不倾倒视频帧，转录文本就是操作界面。
2. **音频优先，视觉跟随。** 剪切点来自语音边界和静音间隙。
3. **询问 → 确认 → 执行 → 自评估 → 持久化。** 未经策略确认，绝不改动剪辑。
4. **不对内容类型作任何假设。** 先查看、再询问、然后编辑。
5. **12 条硬性规则，其余部分保留艺术自由。** 制作正确性不容妥协，审美则可以自由发挥。

完整的制作规则和剪辑技巧请参阅 [`SKILL.md`](./SKILL.md)。
