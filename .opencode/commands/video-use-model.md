---
description: Switch the active video-use agent profile to a different model (gemini|gpt|claude|qwen|grok|kimi|deepseek|default)
---

Run the model switch script with the requested alias: $ARGUMENTS

Execute: `bash /home/josenaves/Projects/video-use/scripts/switch-video-model.sh ${ARGUMENTS:-default}`

Then confirm which profile is now active and remind the user to restart OpenCode (or use `opencode --agent video-use`) for the change to take effect.