---
name: bailian-vision-kit
description: 在 Codex 中安装、迁移或维护"阿里云百炼识图能力"（claude-vision-skill 的 Codex 版）：配置百炼 OpenAI 兼容接口与免费视觉模型列表、免费额度耗尽时自动切换模型、打包迁移到其他电脑、排查识图失败。用户要装到新电脑、换模型、处理额度用尽或识图报错时使用；日常发图片提问不需要本技能，由已装好的 claude-vision-skill 直接处理。
---

# 百炼识图套件（Bailian Vision Kit）

把 asuojun/claude-vision-skill（原本是 Claude Code 脚本）改造成 Codex 可用、且能在免费额度耗尽时自动换模型的识图能力，并把可复用资产打包迁移到其他电脑。

## 部署后的目录形态

```text
<用户目录>\.codex\skills\claude-vision-skill\
|-- SKILL.md                 Codex 技能入口（上游仓库没有，必须补上）
|-- vision.js                识图脚本，含自动换模型逻辑
|-- models.txt               视觉模型列表，一行一个，按顺序切换
|-- config.json              {"api_key": "sk-..."}
|-- .vision-model-state.json 上次成功的模型下标（脚本自动生成）
`-- README.md / CLAUDE.md / cyberboss-setup.md   上游原始文档
```

## 安装到新电脑

1. 把成品目录 `claude-vision-skill/`（发布包里已提供现成版本）复制到 `<用户目录>\.codex\skills\`。
2. 在 `config.json` 里填入百炼 API Key，或改用 `DASHSCOPE_API_KEY` 环境变量（优先级更高）。
3. 确认 Node 可用（`node -v`）；若命令不存在，改用 Codex 自带运行时，见 [references/pitfalls.md](references/pitfalls.md) 第 2 条。
4. 真实调用验证一次，见下节。

逐条命令和目录说明见 [references/install-on-new-machine.md](references/install-on-new-machine.md)。

## 验证

```powershell
powershell -File scripts\verify-vision.ps1        # 自动找 node、生成测试图、真实调用
```

输出识别到的文字即成功；出现“暂不可用…自动切换”说明在换模型，属正常行为。测试图必须够大（800x180 以上），小图会导致 OCR 漏字而误判为故障。

## 关键机制

- 端点：`https://dashscope.aliyuncs.com/compatible-mode/v1`（OpenAI 兼容的 `chat/completions`），可用 `DASHSCOPE_BASE_URL` 覆盖。
- 免费额度按模型独立计算：某模型报 `AllocationQuota.FreeTierOnly` 不等于整个账号不可用，换模型即可继续。
- `vision.js` 从上次成功的模型开始尝试，遇到额度/限流/未开通类错误自动试下一个，成功后就地记住位置。
- 覆盖开关：`VISION_MODEL`（锁定单个模型，不自动切换）、`VISION_MODELS`（逗号分隔的自定义切换列表）。

模型清单、错误码对照和选型建议见 [references/bailian-models.md](references/bailian-models.md)；全部踩坑记录见 [references/pitfalls.md](references/pitfalls.md)。

## 打包与迁移

把 `claude-vision-skill/` 和 `bailian-vision-kit/` 两个目录一起压缩发给对方，对方解压后分别复制进 `.codex\skills\`。发布包里不要带真实 API Key：把 `config.json` 的 `api_key` 清空即可（脚本会提示填写）。
