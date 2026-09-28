# 百炼视觉模型、额度和错误码

## 调用方式

- 兼容端点：`https://dashscope.aliyuncs.com/compatible-mode/v1`，接口 `POST /chat/completions`（OpenAI 格式）。
- 图片通过 `image_url` 传入：本地文件转成 base64 data URL，公网图片可直接给 URL（脚本的 `--url`）。
- 计费按量，单次约几分钱；不同模型的免费额度相互独立，用完一个不影响其他。

## 错误码对照

| 返回 | 含义 | 处理 |
| --- | --- | --- |
| `AllocationQuota.FreeTierOnly` | 该模型免费额度已用尽 | 换列表里的下一个模型 |
| `Throttling` / `Throttling.RateQuota` | 限流 | 稍后重试或换模型 |
| `AccessDenied` + 消息含 free tier / exhausted | 免费层不可用 | 换模型 |
| `InvalidApiKey` | Key 无效 | 检查 `config.json` / 环境变量 |
| 模型不存在 / 未开通 | 模型名写错或未开通 | 校正 `models.txt` 中的名字 |

前三类都会让 `vision.js` 自动切换下一个模型；`InvalidApiKey` 属于账号级问题，换模型也没用。

## 模型列表（models.txt，顺序即尝试顺序）

```text
qwen3.6-plus                qwen3.7-plus               qwen3.7-flash-2026-07-15
qwen3.6-flash               qwen3.6-flash-2026-04-16   qwen3.7-flash
qwen3-vl-flash              qwen3-vl-flash-2026-01-22  qwen3-vl-flash-2025-10-15
qwen3-vl-30b-a3b-instruct   qwen3-vl-32b-instruct      qwen3-vl-235b-a22b-instruct
qwen3-vl-plus-2025-09-23    qwen3-vl-plus              qwen3.5-ocr
qwen3.5-flash               qwen3.5-flash-2026-02-23   qwen3.5-plus
qwen3.5-plus-2026-02-15     qwen3.5-plus-2026-04-20    qwen-vl-plus
qwen-vl-max                 qwen3-vl-8b-instruct       kimi-k2.5
kimi-k2.6                   kimi-k2.7-code
```

共 26 个，取自百炼控制台的可用视觉模型列表。滚动名（如 `qwen3.7-flash`）指向最新快照，快照名（如 `qwen3.7-flash-2026-07-15`）固定到某天，两者的免费额度分开计算，所以都留在列表里。

## 选型建议

- 要快：`*-flash`、`qwen3-vl-flash` 系列，实测单次 1-3 秒。
- 要准：`qwen3-vl-235b-a22b-instruct`、`qwen-vl-max`，适合密集界面截图。
- 纯 OCR：`qwen3.5-ocr`。
- 思考型（`qwen3-vl-235b-a22b-thinking`）单次约 45 秒，调用超时要放到 180 秒以上，建议只在前面的模型都不可用时才用。

## 本机实测消耗记录

| 日期 | 情况 |
| --- | --- |
| 2026-08-05 | `qwen3.7-flash-2026-07-15` 可用，配置完成 |
| 2026-08-15 | 该模型免费额度用尽 |
| 2026-08-16 | `qwen3.7-plus` 用尽，换 `qwen3.6-plus` |
| 2026-08-17 | `qwen3.6-plus` 用尽，换 `qwen3.6-flash` |
| 2026-08-19 | `qwen3.6-flash` 用尽，换 `qwen3-vl-235b-a22b-thinking`；随后改成模型列表自动切换，落到 `qwen3.6-flash-2026-04-16` |
| 2026-09-26 | 列表前 11 个模型全部报 `AllocationQuota.FreeTierOnly`，自动落到 `qwen3-vl-235b-a22b-instruct` |

结论：免费额度会随使用逐个耗尽，靠列表 + 自动切换可以长期免维护。
