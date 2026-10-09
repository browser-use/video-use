# srt_video_editor 项目说明

本项目是电视剧解说自动剪辑工具。

核心目标：
根据 script.srt 和 edit_plan.json，从 source.mp4 中截取画面，拼接、加配音、烧字幕，输出 final.mp4。

当前阶段：
只做 SRT 驱动剪辑，不做 AI 自动理解剧情。

技术要求：
- Python 3.10+
- ffmpeg
- Windows 优先
- 路径尽量使用英文
- 不使用 moviepy，优先直接调用 ffmpeg
- 输出日志要清楚
- 不要引入复杂前端

核心输入：
- input/source.mp4
- input/script.srt
- input/edit_plan.json
- input/voice.wav

核心输出：
- output/final.mp4

禁止事项：
- 不要破解剪映
- 不要调用未授权接口
- 不要一次性做复杂 AI 自动分析
- 不要改动 input 原始文件
