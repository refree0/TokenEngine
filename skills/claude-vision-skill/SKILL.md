---
name: claude-vision-skill
description: 为不具备原生视觉能力的模型提供识图能力。当用户分享本地图片路径或网络图片 URL，或要求分析、描述、识别图片内容，或消息中出现 "Saved attachments:" 时使用。运行 `node vision.js "<图片路径或URL>" "<问题>"` 将图片交给视觉模型（阿里云百炼）返回文字描述，不要用 Read 等工具直接读图片文件。
---

# 识图能力

你的底层模型不具备原生识图能力。遇到图片时，不要用 Read 等工具直接读取图片文件，改用 `vision.js`：

```bash
node vision.js "<图片路径>" "请用中文描述这张图片的内容"
```

## 触发场景

- 用户分享本地图片路径或网络图片 URL
- 消息中出现 "Saved attachments:" 并列出图片
- 用户要求分析、描述、识别图片内容

## 用法

- 本地图片：`node vision.js "C:\path\to\image.png" "问题"`
- 网络图片：`node vision.js --url "https://example.com/image.png" "问题"`
- 不带问题时，默认详细描述图片内容

对每张图片依次执行，拿到全部描述后再统一回复；识别失败时向用户报告错误信息。

## 配置（已配置完成）

- API 服务：阿里云百炼（DashScope OpenAI 兼容接口）
- 模型：见同目录 `models.txt`（一行一个，按顺序切换）
- API Key 放在同目录 `config.json` 的 `api_key` 字段；也可用 `DASHSCOPE_API_KEY` 环境变量覆盖（优先级更高），端点可用 `DASHSCOPE_BASE_URL` 覆盖

## 自动切换模型

- 每次识图从上次成功的模型开始尝试；如果该模型额度用尽或不可用（API 返回限流/额度/欠费/未开通等错误），自动按 `models.txt` 顺序尝试下一个模型。
- 成功后会把当前模型的位置记入 `.vision-model-state.json`，下次直接从那继续。
- 如需临时固定某个模型，可设置 `VISION_MODEL` 环境变量（此时不自动切换）；如需自定义切换列表，可设置 `VISION_MODELS` 环境变量（逗号分隔）。
