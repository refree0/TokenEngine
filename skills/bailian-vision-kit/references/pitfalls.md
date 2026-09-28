# 踩坑记录

## 1. 上游仓库是 Claude Code 技能，不是 Codex 技能

asuojun/claude-vision-skill 根目录只有 `vision.js`、`CLAUDE.md`、`README.md`、`cyberboss-setup.md`，没有 `SKILL.md`。

- 直接复制进 `.codex\skills\` 不会被 Codex 识别成技能，必须补一个带 YAML frontmatter（`name` + `description`）的 `SKILL.md`。
- 用官方技能安装器装会直接失败：`Error: SKILL.md not found in selected skill directory`。要装就得手工复制再补 `SKILL.md`。

## 2. node 不在 PATH

脚本是 Node 程序，但很多机器没装 Node 或没加进 PATH，报错是 `node : 无法将“node”项识别为 cmdlet...`。用 Codex 桌面版自带运行时即可：

```text
C:\Users\<用户名>\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe
```

版本号目录会随 Codex 升级变化，可用 workspace dependencies 查询当前路径。调用时用绝对路径最稳。

## 3. “额度用尽”是业务错误，不是网络异常

返回的是 HTTP 400/429 加 JSON：`{"error":{"code":"AllocationQuota.FreeTierOnly", ...}}`。只看状态码会误判成“接口坏了”，必须解析 `error.code` / `error.message` 才能决定是否换模型。免费额度按模型独立计算，换一个模型通常立刻可用。

## 4. 不记录进度就会每次从头重试

如果每次调用都从列表第一个模型开始，额度耗尽的模型会被反复试错，每次多花几秒。用 `.vision-model-state.json` 记下成功的下标，下次直接从那里开始。

## 5. API Key 写死在代码里

上游 README 的做法是把 `sk-xxx` 直接替换进 `vision.js`。坏处：难维护、一分享就泄露、换 key 要改代码。本套件改成读同目录 `config.json`（环境变量优先级更高），发布包里只放 `config.example.json`。

## 6. 测试图太小会误判成识别坏了

实测 360x140、28px 字号的图，模型只读出 `Codex Vision Test 12`（漏字）；换成 800x180、36px 后完整读出 `Codex Vision Test 12345`。验证时必须生成够大的测试图，否则会白折腾。

## 7. 思考型模型很慢

`qwen3-vl-235b-a22b-thinking` 单次约 45 秒，默认 30 秒超时会导致误报失败。给这类模型的调用超时至少 180 秒，并且别把它排在列表最前面。

## 8. 中文环境读 UTF-8 文件会乱码

Windows PowerShell 的 `Get-Content` 不加 `-Encoding UTF8` 会按 GBK 解析，中英文混排的技能文件看起来像乱码，容易被误判成文件损坏。读中文文本一律加 `-Encoding UTF8`。

## 9. 新技能不是装完立刻可用

Codex 在下一轮对话才加载技能列表。刚复制完技能、当轮看不到它属正常，下一轮就会出现在可用技能里。

## 10. 免费额度耗尽后会自动滑向付费模型

列表后面的模型若没有免费额度，调用会真实计费（每次几分钱）。不想付费时：把不想用的模型从 `models.txt` 删掉，或在百炼控制台给 Key 设额度上限。

## 11. 别把带 Key 的包发出去

打包迁移时排除本机状态 `.vision-model-state.json`，并把 `config.json` 的 `api_key` 清空（脚本会提示填写），避免真实 Key 随包外流。

## 12. PowerShell 默认禁止运行 .ps1 脚本

直接 `powershell -File verify-vision.ps1` 会报 `running scripts is disabled on this system`（执行策略 Restricted）。加参数绕过即可：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-vision.ps1
```

## 13. 中文 Windows 下 Python 按 GBK 读 UTF-8 文件会崩

技能文件含中文时，Python 脚本可能直接报 `UnicodeDecodeError: 'gbk' codec can't decode byte ...`（实测 skill-creator 自带的 `generate_openai_yaml.py`、`quick_validate.py` 都会）。加环境变量强制 UTF-8 模式：

```powershell
$env:PYTHONUTF8 = "1"
python <脚本>
```

同理，PowerShell 读写中文文本要显式 `-Encoding UTF8`（见第 8 条）。
