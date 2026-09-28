# 在新电脑上安装识图能力

适用：Windows + Codex 桌面版；已有一个阿里云百炼 API Key（在 <https://bailian.console.aliyun.com/> 控制台创建）。

## 1. 复制技能目录

把发布包里的 `claude-vision-skill` 整个文件夹复制到：

```text
C:\Users\<用户名>\.codex\skills\
```

目标目录不存在时先创建。复制完成后应当存在：

```text
C:\Users\<用户名>\.codex\skills\claude-vision-skill\SKILL.md
```

注意：只复制 `vision.js` 没用，Codex 靠 `SKILL.md` 把目录识别成技能。

## 2. 填 API Key

编辑 `claude-vision-skill\config.json`：

```json
{ "api_key": "sk-你的Key" }
```

也可以用环境变量 `DASHSCOPE_API_KEY`，它的优先级高于 `config.json`。不要把自己的 Key 连文件一起发给别人。

## 3. 确认 Node.js

```powershell
node -v
```

如果提示找不到命令，用 Codex 桌面版自带的 Node（版本号目录会随 Codex 升级变化，可用 Codex 的 workspace dependencies 查询当前路径）：

```text
C:\Users\<用户名>\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

调用脚本时用这个绝对路径，或把 Node 加入系统 PATH。

## 4. 验证

```powershell
powershell -ExecutionPolicy Bypass -File <发布包>\bailian-vision-kit\scripts\verify-vision.ps1
```

`-ExecutionPolicy Bypass` 是必需的：Windows 默认执行策略会拒绝运行 .ps1 文件。

默认按 `%USERPROFILE%\.codex\skills\claude-vision-skill` 找脚本，也可传参：

```powershell
powershell -File verify-vision.ps1 -SkillDir "D:\skills\claude-vision-skill" -NodeExe "C:\path\to\node.exe"
```

手工验证等价于：

```powershell
& "<node路径>" "<技能目录>\vision.js" "<测试图路径>" "请逐字读出图片中的全部文字内容"
```

## 5. 生效

Codex 在下一轮对话才加载技能列表，所以装完当轮看不到属正常。之后直接在对话里发图片，Codex 会调用 `vision.js` 识别；也可以在命令行直接用它识图。

## 常见后续操作

- 换模型 / 调整顺序：编辑 `models.txt`，一行一个模型名，顺序即尝试顺序。
- 重置切换起点：删除 `.vision-model-state.json`，下次从列表第一个开始。
- 固定单个模型：`$env:VISION_MODEL = "qwen3-vl-plus"`。
