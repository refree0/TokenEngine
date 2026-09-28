# 百炼识图套件 · 使用说明

让 Codex 能用阿里云百炼的视觉模型识别图片，免费额度用完时自动切换下一个模型。

## 第一步：复制两个文件夹

把包里这两个文件夹整个复制到 Codex 的技能目录：

| 文件夹 | 作用 |
| --- | --- |
| `claude-vision-skill` | 识图技能本体，Codex 实际调用它识别图片 |
| `bailian-vision-kit` | 安装/迁移/换模型/排障指南，含验证脚本 |

目标目录（不存在就先创建）：

```text
C:\Users\<你的用户名>\.codex\skills\
```

复制完成后应存在：`...\.codex\skills\claude-vision-skill\SKILL.md`。

## 第二步：填 API Key

编辑 `claude-vision-skill\config.json`，把 key 填进 `api_key`：

```json
{ "api_key": "sk-你的Key" }
```

Key 获取：阿里云百炼控制台 <https://bailian.console.aliyun.com/> → API-KEY 管理 → 创建 → 复制 `sk-` 开头的字符串。

> 出于安全考虑，包里**没有**内置任何 Key，需要你自己填。

## 第三步：验证

```powershell
powershell -ExecutionPolicy Bypass -File "C:\Users\<你的用户名>\.codex\skills\bailian-vision-kit\scripts\verify-vision.ps1"
```

看到识别出的文字（`Vision Check 24680`）就成功了。`-ExecutionPolicy Bypass` 不能省，Windows 默认禁止运行 .ps1 脚本。

装好后，Codex 会在**下一轮对话**才加载到这个技能；之后在对话里直接发图片即可自动识别。

## 关于免费额度和自动换模型

- 百炼的免费额度**按模型独立计算**，某个模型用完不影响其他模型。
- `models.txt` 里列了 26 个可用视觉模型，`vision.js` 会从上次成功的模型开始，遇到“额度用尽 / 限流 / 未开通”自动换下一个，成功后就地记住位置。
- 所以一般不需要手动换模型。想调整顺序就编辑 `models.txt`（一行一个模型名，顺序即尝试顺序）；想重新从头试，删掉 `.vision-model-state.json`。
- 提醒：列表靠后的模型如果没有免费额度，会产生按量费用（每次约几分钱）。不想付费就把不用的模型从 `models.txt` 删掉，或在控制台给 Key 设置额度上限。

## 前置条件

- Windows + Codex 桌面版
- 系统没有 Node.js 也能用：脚本会自动找 Codex 自带运行时

## 想深入了解

- 安装到新机器的逐条步骤：`bailian-vision-kit\references\install-on-new-machine.md`
- 踩坑与排障（13 条）：`bailian-vision-kit\references\pitfalls.md`
- 模型清单、错误码对照、实测额度消耗：`bailian-vision-kit\references\bailian-models.md`
