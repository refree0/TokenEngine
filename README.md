# TokenEngine

> 跑在本机的 **OpenAI 兼容网关**：把多个大模型免费额度聚合成**一个地址**，
> 自动选源、自动换账号、自动冷却恢复。任何一个源挂了或撞限流，请求会静默切到下一个。

给本机（或内网）所有 CLI Agent —— Codex、Claude Code、Cline、Hermes、Pi 以及任何
OpenAI 兼容客户端 —— 一个统一入口。上层 Agent 永远只连 `http://127.0.0.1:8317/v1`，
底下换哪家源、哪个账号，它不需要知道。

---

## 为什么做这个

单家免费额度总有三个坑：

| 坑 | 表现 |
|---|---|
| **额度小** | 一次深度重构就跑光，然后 Agent 卡死 |
| **会限流** | 429 来得突然，且各家口径不同（有的按 RPM，有的按 TPM） |
| **会失效** | 免费政策说变就变，某天醒来发现源已经下线 |

TokenEngine 的思路是把**横向广度**（多源聚合）和**纵向深度**（同源多账号 + 主动限速）合在一起，
让"免费额度"从一次性消耗品变成可持续的日常燃料。

---

## 核心特性

- **三层路由**：选源 → 选账号 → 选模型。每层都可独立降级。
- **账号池**：同一 provider 下挂多个账号，429/403 时自动换账号；额度按账号独立聚合。
- **AIMD 自适应限速**：撞限流后主动降速（乘 0.85），平稳后缓慢回升（+0.05），避免反复撞墙。
- **冷却联动**：一个账号 429，同 provider 内其他账号感知并错峰，不连锁打爆。
- **三协议兼容**：OpenAI Chat Completions / Anthropic Messages / OpenAI Responses。
- **Agent 场景工具优先**：带工具调用的请求不会因为历史里出现过截图就被降级到视觉模型，
  避免弱视觉模型的工具调用能力拖垮整个 coding 会话。
- **工具参数防漂移**：自动剥离上游模型误加的 `{"arguments": {...}}` 包装层，
  防止客户端解析不到 `cmd` 之类的字段。
- **上下文窗口感知**：按估算输入 token 跳过装不下的源；所有源都装不下时**明确报错**
  并提示拆分请求，而不是把超长请求挨个砸进小窗口源、白撞一串 400。
- **截断自动补救**：上游 `finish_reason=length` 且正文为空（推理模型把输出预算全花在思考上）
  时，同源放宽 `max_tokens` 重试一次，成功就直接用——不白烧一次冷却和额度。
- **零依赖**：纯 Python 标准库，`python router.py` 就能跑。
- **网页控制台**：内置单页控制台（`http://127.0.0.1:8317/`），点按钮就能启停源、
  调优先级、重置冷却、做连通性测试，不用改配置文件、不用敲命令。
- **健康看板**：`/health` 与 `/api/metrics` 暴露每个源的账号状态、冷却剩余时间、近期成功率。
- **看门狗 + 哨兵**：`watchdog.py` 保活，`sentinel.py` 定期探测各源健康度。

---

## 架构

```
        CLI Agents（本机 / 内网其他机器 / 手机）
                        │
                        │  base_url = http://127.0.0.1:8317/v1
                        ▼
    ┌──────────────────────────────────────────────┐
    │   router.py   (ThreadingHTTPServer :8317)    │
    │                                              │
    │   选源 ──▶ 选账号 ──▶ 选模型 ──▶ 转发        │
    │    │          │           │                  │
    │    │          │           └─ model_map 映射  │
    │    │          └─ 429/403 自动换号 + 冷却     │
    │    └─ priority 排序，坏源自动跳过            │
    └──────────────────────────────────────────────┘
              │         │         │         │
        ┌─────┴───┐ ┌───┴────┐ ┌──┴───┐ ┌───┴────┐
        │ 源 A    │ │ 源 B   │ │ 源 C │ │ 源 N   │  … 按 priority 依次尝试
        └─────────┘ └────────┘ └──────┘ └────────┘
```

---

## 快速开始

```bash
# 1) 拉代码
git clone <this-repo> && cd TokenEngine

# 2) 生成自己的配置（模板不含任何密钥）
cp config/providers.example.json config/providers.json

# 3) 编辑 config/providers.json，把各源的 api_key 填进对应位置
#    （enabled=false 即下线该源；priority 越小越优先）

# 4) 启动
python router.py
#  或双击 start_router.bat
```

> 还没有 key？见下方 **[免费源申请指南](#免费源申请指南量大管饱版)** —— 已按额度大小排好优先级，含入口链接与多账号说明。

验证：

```bash
curl http://127.0.0.1:8317/health
curl http://127.0.0.1:8317/v1/models
```

---

## 免费源申请指南（量大管饱版）

> **信息截至 2026-09-27**，注册前请以各官方页面为准。
> 免费政策变动很频繁 —— 过去 4 个月里，Cerebras（改为 $5 一次性试用 + 绑卡）、
> GitHub Models（已退役）、Together（最低 $5）、SambaNova（停止赠额）已相继取消免费额度。
> 本节按「**额度大小 × 可持续性**」排序，只列能长期、稳定拿到量的渠道。

### 先记住一条：账号数由「限流维度」决定，不是越多越好

| 限流维度 | 典型平台 | 多开账号有用吗 |
|---|---|---|
| **按 IP 计** | AMD Radeon Cloud | ❌ 同机共享一个 IP 配额（每 IP 120 RPM 是硬顶） |
| **按组织计** | Groq | ❌ 同账号建 10 个 Key 也共享一套限额 |
| **官方明确不扩容** | OpenRouter | ❌ 文档写明"额外 Key 或账号不会提高平台限额" |
| **按账号独立** | 商汤 sensenova、火山方舟 | ✅ 线性叠加，值得开 |

> 各平台对多账号的条款不同，请自行确认并遵守其服务条款。

**怎么用实测数据判断该不该加号**：跑一段时间后翻 `logs/fail.log`，看 429 的**原文**——
错误信息里写的是"账号"还是"模型/IP"，直接决定加号有没有用：

| 错误原文里的关键词 | 限流维度 | 加号有用吗 |
|---|---|---|
| `Your account [xxx] has reached the set usage limit` | 按账号 | ✅ 有用 |
| `Free quota exhausted` / `余额不足或无可用资源包` | 按账号额度 | ✅ 有用 |
| `inference exceeds tpm/rpm limit`（含 `ModelAccountTpm`） | 按账号 | ✅ 有用 |
| `Model 'xxx' is at its concurrency limit (64)` | **按模型并发** | ❌ 无用 |
| `TimeoutError` / 读超时 | 服务端速度 | ❌ 无用 |

> 实测教训：某个源 429 率高达 17%，看着像"限流严重该加号"，
> 但错误原文是 `Model 'X' is at its concurrency limit` —— 那是**按模型**的并发上限，
> 多开账号一分钱收益都没有，真正的瓶颈是服务端慢（超时率 17.5%）。
> **别只看 429 的次数，要看 429 在说什么。**

另一个现实约束：**真正的物理瓶颈是手机号和实名**。阿里云个人实名、魔搭绑定阿里云都不可逆，
所以"多账号"能走多远，取决于手上有几个手机号。

### 额度总览（按量大管饱排序）

| 优先级 | 平台 | 量级 | 限流维度 | 建议账号数 | 前置要求 |
|---|---|---|---|---|---|
| **P0** | 阿里云百炼 | **每模型各 100 万 tokens / 180 天**，可选上百个模型 | 按账号 | **2–3**（实测 403 `Free quota exhausted`，按账号计） | 阿里云实名 |
| **P0** | 魔搭 ModelScope | **≈2000 次/天**，每天刷新 | 按账号 | 1 | 手机/支付宝 + 阿里云实名 |
| **P0** | 商汤 sensenova | 60k 积分 / 5h，**可再生** | 按账号独立 | **2–3**（实测 429 率 28.9%，且可再生 → 线性叠加） | 手机号 |
| **P0** | NVIDIA NIM | **无 token 总量上限**，40 RPM | 按账号 | 1 | +86 手机验证 |
| **P1** | 阶跃星辰 Stepfun | 100 万 tokens（30 天，可延长至 75 天） | 按账号 | 1 | 手机号 |
| **P1** | 国家超算 SCNet | 1000 万 tokens（一次性） | 按账号 | 1 | 实名 |
| **P1** | AMD Radeon Cloud | $1/天 + 20 RPM / 账号 | 按 IP 硬顶 120 RPM | 2–3（最多 6，**跨 IP 才有意义**） | — |
| **P1** | 智谱 bigmodel / Z.ai | GLM Flash 系**全免费** | 按账号 | **1–2**（实测 429 `余额不足`；但 16k 窗口是硬伤） | — |
| **P1** | Google AI Studio | Gemini Flash 30 RPM / 1500 次/天 / 100 万 TPM | 按账号 | 1 | 需代理 |
| **P1** | Groq | 20 万 tokens/天 | 按组织 | 1（不够加 2–3） | 需代理 |
| **P1** | Cloudflare Workers AI | 10,000 Neurons/天（≈14.7 万 tokens） | 按账号 | 1 | 需代理 |
| **P2** | Mistral | $10/月 | 按账号 | 1 | 需代理 + 手机验证 |
| **P2** | OpenRouter | 50 次/天（充 $10 → 1000 次/天） | 按账号 | 1（多账号无效） | 需代理 |
| **P2** | 硅基流动 | 每日免费开源模型额度 | 按账号 | **1**（实测失败全是 `TimeoutError`，加号无效） | — |
| **P2** | 火山方舟 | 新用户 50 万 tokens 体验包 | 按账号独立 | **2–3**（实测 429 率 59.6%，原文明确写 `Your account [...]`） | 实名 |
| **P2** | Cohere | 1000 次/月 | 按账号 | 1 | — |
| **P2** | HuggingFace | $0.10/月 | — | 0–1 | — |

**只做 P0 这 4 个账号**，网关上游基本就够用了。

### 哪些源能长期当主力？（量大 + 可再生 + 可多账号）

同时满足这三个条件，才算"能当长期主力"。逐源核对：

| 源 | 量大管饱 | 额度可再生 | 多账号叠加 | 结论 |
|---|---|---|---|---|
| **商汤 sensenova** | ✅ 60k 积分 / 5h | ✅ 按 5h 恢复 | ✅ 按账号，官方允许 | ⭐ **无条件满足** |
| **NVIDIA NIM** | ✅ 无 token 上限 | ✅ 40 RPM 常态 | ✅ 按账号（另有免费提额通道） | ⭐ **无条件满足** |
| 魔搭 ModelScope | ✅ 2000 次/天 | ✅ 每天 | ⚠️ 额度叠加，但**并发限 1** | 额度池大，吞吐受限 |
| AMD Radeon Cloud | ⚠️ $1/天 | ✅ 每天 | ⚠️ **每 IP 120 RPM 硬顶** | 同机最多 6 个 |
| 智谱 bigmodel | ⚠️ Flash 系免费 | ✅ 每天 | ⚠️ 限额不透明 | 兜底用 |
| Groq | ⚠️ 20 万 tokens/天 | ✅ 每天 | ✅ 按组织（多账号有效） | 量偏小 |
| Google AI Studio | ✅ 1500 次/天 | ✅ 每天 | ⚠️ **按项目计**（同账号多项目无效） | 需代理 |
| 阿里云百炼 | ✅ 每模型 100 万 | ❌ 180 天周期 | ⛔ 官方建议别多开 | 非每日，但总量最大 |
| 阶跃星辰 Stepfun | ⚠️ 100 万 tokens | ❌ 30 天有效期 | — | 一次性 |
| 国家超算 SCNet | ✅ 1000 万 tokens | ❌ 一次性 | — | 一次性 |
| 火山方舟 | ⚠️ 50 万 tokens | ❌ 一次性 | ✅ 按账号 | 一次性 |
| OpenRouter | ❌ 50 次/天 | ✅ 每天 | ⛔ 官方明文不扩容 | 不可扩容 |
| 硅基流动 / Cohere / HuggingFace | ❌ 量太小 | ✅ | — | 仅兜底 |

**结论：只有商汤 sensenova 和 NVIDIA NIM 是无条件满足三个条件的。**
其余要么额度不可再生（一次性或长周期），要么多账号受 IP / 组织 / 项目维度限制。

---

### P0 · 立刻注册（4 个，全部国内直连、免绑卡）

#### 1. 阿里云百炼 —— 总量最大

- **控制台**：https://bailian.console.aliyun.com/ （用阿里云账号登录，需个人实名）
- **额度**：**每个模型独立发放免费额度**（新用户通常每模型 100 万 tokens / 180 天）
- **接入**：`https://dashscope.aliyuncs.com/compatible-mode/v1`

💡 **这是总量最大的源** —— 因为额度**按模型 Code 独立发放**，逐个耗尽互不影响。
一个账号就能覆盖上百个模型，相当于上百份独立额度。

⛔ **不要多注册账号**。正确做法是在**一个账号里多挑没用过的模型**，
去控制台的「模型广场」逐个启用。多开账号收益远不如把模型铺开。

⚠️ 免费额度会**逐个耗尽**（某个模型用完了会返回 403，但其他模型不受影响）。
选模型前先查控制台的「免费额度」页，或直接用本仓库的 `probe_bailian_models.py` 批量探活。

#### 2. 魔搭 ModelScope —— 单日调用次数最高

- **注册**：https://www.modelscope.cn/ （建议手机号或支付宝登录，后面实名信息能对上）
- **访问令牌**：https://www.modelscope.cn/my/myaccesstoken
- **绑定阿里云**：头像 → 账号设置 → 「绑定阿里云账号」
- **阿里云实名**：https://account.aliyun.com/ → 账号管理 → 个人认证（支付宝扫码最快）
- **额度**：约 **2000 次/天**，每天刷新；**单模型有上限**（实测 200–500 次/天）
- **Key 格式**：`ms-` 开头（选 Write 权限），只显示一次
- **接入**：`https://api-inference.modelscope.cn/v1`

⚠️ **官方并没有承诺"2000 次/天"这个数字**。文档原文是"对使用额度和并发会进行一定限制，
同时根据实时资源使用情况，会**实时进行动态调整**"。2000 次是社区实测的经验值，
且额度会在各模型间**动态分配**（热门模型分得少、冷门模型分得多）。

⛔ **真正的硬约束是并发，不是额度**。官方原文："不同模型允许的调用并发……
原则上以**保障开发者单并发正常使用**为目标"，并要求"**请勿用于需要高并发以及
SLA 保障的线上任务**"。所以**不要给魔搭账号配 `max_concurrency` > 1**，
也不要指望靠多账号线性提升吞吐。

💡 **多账号的真相**：官方只提"云账号（阿里云账号）"这一个维度，**没有 IP 维度限制**，
所以多个阿里云账号理论上各自独立计数。但收益是**额度叠加、吞吐不叠加**。
而且魔搭自我定位是"非商业化、非盈利，为了在有限平台资源下最大范围服务开发者" ——
多账号跑满会消耗平台留给所有人的公平性配额，**建议 1 个够用，最多 2 个**。

💡 **官方给的扩容正路**：文档明确"如果需要高并发、大额度的调用，可以考虑通过
**API-Provider** 绑定外部 API 提供方"。另外魔搭有**魔粒**体系 ——
"魔粒余额充足即可调用任意次数"（轻量 0.5 / 主流 1 / 旗舰 2 魔粒每次）。

⚠️ **绑定阿里云不可逆** —— 一个魔搭账号只能绑一个阿里云账号。也就是说每个魔搭账号
= 一个阿里云账号 + 一次个人实名 + 一个手机号。

#### 3. 商汤 sensenova —— 唯一"可再生 + 按账号独立"的源

- **开放平台**：https://platform.sensenova.cn/
- **额度**：**60k 积分 / 5h**，用完到点恢复
- **接入**：`https://token.sensenova.cn/v1`

💡 **最适合多开账号的源** —— 同时满足「额度可再生 + 按账号独立计费 + 官方允许多账号」，
多账号收益**直接线性叠加**。跑满之后再加第 2 个，一般 2–3 个就够。

⚠️ 撞到的 429 大多是**每分钟 TPM/RPM 短期限流**，稍等即恢复，**不是额度耗尽** ——
别急着换源，先看 `/health` 里的冷却剩余时间。

⚠️ **同一个账号建多个 Key 是无效的**（额度按账号算，不按 Key 算）。

#### 4. NVIDIA NIM —— 无 token 总量上限

- **注册**：https://build.nvidia.com/
- **Key 页面**：https://build.nvidia.com/settings/api-keys （找不到就走右上角头像 → API Keys）
- **模型目录**：https://build.nvidia.com/explore/discover
- **额度**：**无 token 总量上限**，40 RPM（全模型共享）
- **前置**：+86 手机验证码 —— **不验证的话 Key 无效**
- **Key 格式**：`nvapi-` 开头，只显示一次
- **接入**：`https://integrate.api.nvidia.com/v1`

💡 **不要急着多注册**。NVIDIA 官方论坛有现成的**免费提额通道**：
在 https://forums.developer.nvidia.cn/ 的「访问与账号」版块发帖，申请把 40 RPM 提到 200 RPM。
提额成功一次 = 顶 5 个账号，比养多个号划算得多。只有提额被拒、且确实跑满 40 RPM 时，
再考虑第二个账号。

---

### P1 · 本周内注册

#### 5. 阶跃星辰 Stepfun —— 国内直连，量不错

- **注册 / 控制台**：https://platform.stepfun.com/ · 在线试用 https://studio.stepfun.com/
- **额度**：新用户 **100 万 tokens / 30 天** + 99 元套餐 75 天活动
  （登录 15 天 + 首次调用 15 天 + 每邀 1 好友 15 天，封顶 75 天）
- **限流**：免费档 60 RPM / 100,000 TPM
- **接入**：`https://api.stepfun.com/v1` （是 `api.` 不是 `platform.`）
- **模型**：`step-5-preview`（**1M 上下文**、文本 + 图片 + 视频）· `step-3.7-flash` · `step-3.5-flash`
- **视觉**：✅ 支持图片 URL / Base64（最多 60 张）+ 视频（MP4 < 128MB）

#### 6. 国家超算互联网 SCNet —— 双协议

- **注册**：https://www.scnet.cn/
- **Key 管理**：https://www.scnet.cn/ui/console/index.html#/llm/apikeys
- **接入文档**：[apicall](https://www.scnet.cn/ac/openapi/doc/2.0/moduleapi/tutorial/apicall.html) · [quickstart](https://www1.scnet.cn/ac/openapi/doc/2.0/moduleapi/tutorial/quickstart.html)
- **额度**：新用户 **1000 万 tokens**（**一次性**，用完不恢复）
- **接入**：
  - OpenAI 兼容 `https://api.scnet.cn/api/llm/v1`
  - Anthropic 兼容 `https://api.scnet.cn/api/llm/anthropic`
- **模型**：MiniMax-M2.5、DeepSeek-V3.2、Qwen3-235B-A22B、DeepSeek-R1-0528

⛔ **关键：创建 Key 时选"通用"服务类型，不要买 Coding Plan / Token Plan。**
官方文档明文规定，套餐专属 Key（`sk-sp-` / `sk-tp-` 开头）
"仅限在 AI 编程工具中使用，不可用于 API 调试、工作流平台等非交互式场景"。
接进本网关会被判定为按量计费并**额外扣费** —— 和火山 Plan 是同一个坑。
**只有通用 Key（`sk-` 开头）能进网关。** 创建时"服务类型"选通用、"访问模型范围"建议全选。

#### 7. AMD Radeon Cloud —— 免费 Beta，国内直连免绑卡

- **入口**：https://developer.amd.com.cn/radeon/
- **额度**：每账号 **$1/天** + 20 RPM，每天再生
- **接入**：`https://developer.amd.com.cn/radeon/api/v1`
- **实测**：DeepSeek-V4-Flash（1M 上下文）约 2.1s；视觉用 `DeepSeek-V4-Flash-Vision-Exp`

⚠️ **每 IP 120 RPM 是硬顶** —— 同一台机器上开 6 个账号就吃满 IP 上限，**再多无收益**。
建议 2–3 个。
⚠️ 免费端点**不提供 `/v1/responses`**（返回 404），走 `chat/completions` 即可。
⚠️ 模型清单是 Beta，会变，以 `GET /v1/models` 为准。

#### 8. 智谱 bigmodel / Z.ai —— Flash 系全免费

- **国内站**：https://open.bigmodel.cn/ → Key https://open.bigmodel.cn/usercenter/apikeys
- **国际站**：https://z.ai/ → Key https://z.ai/manage-apikey/apikey-list
- **额度**：`GLM-4.7-Flash` / `GLM-4.5-Flash` / `GLM-4.6V-Flash` 输入输出**全免费**
- **实测**：`glm-4.5-flash` 文本响应约 317ms，稳定；`glm-4v-flash` 是免费视觉模型

⚠️ 官方**未公布速率限额**，不能当可规划的配额。限额不透明，多注册没有可预期收益。
国内站和国际站可以当两条独立的路。

#### 9–11. 需代理的三个（额度中等，当补充）

| 平台 | 入口 | 额度 | 接入 |
|---|---|---|---|
| **Google AI Studio** | Key https://aistudio.google.com/apikey （Google 账号登录，**不要信用卡**） | Gemini 1.5 Flash：30 RPM / 1500 次/天 / 100 万 TPM<br>Gemini 2.0 Flash：10 RPM / 1000 次/天 / 40 万 TPM | `https://generativelanguage.googleapis.com/v1beta/openai/` |
| **Groq** | 控制台 https://console.groq.com/ · Key https://console.groq.com/keys | 30 RPM / 1000 次/天 / 8K TPM / **20 万 tokens/天** | `https://api.groq.com/openai/v1` |
| **Cloudflare Workers AI** | 注册 https://dash.cloudflare.com/sign-up · Token https://dash.cloudflare.com/profile/api-tokens | 10,000 Neurons/天（00:00 UTC = 北京 08:00 重置），文本生成 300 RPM<br>gpt-oss-120b ≈ 14.7 万 tokens/天 | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions` |

⚠️ **Gemini 额度最充裕**，是国内直连返回 000（不通），**必须走代理**。
⚠️ **Groq 的真瓶颈是 20 万 tokens/天** —— 均值仅 200 token/请求，实际约 100 次就用光。
限流**按组织计**，多 Key 无用、多账号有效。它的价值是首 token 极快（约 234ms）。
⚠️ **Cloudflare 需要 `account_id`**（面板右下角可复制），国内访问时通时断，建议挂代理。

---

### P2 · 补充与兜底

| 平台 | 入口 | 额度 | 说明 |
|---|---|---|---|
| **Mistral** | https://console.mistral.ai/ · Key `/api-keys` | $10/月（约每天 $0.33） | ⚠️ 必须完成**手机验证**，否则 Key 报"授权类"错误而非"配额类"，容易误判成代码问题。独特价值是 Codestral / Devstral 代码专用模型。免费层与付费层条款不同（数据留存/训练政策），敏感代码别发 |
| **OpenRouter** | https://openrouter.ai/ · Key `/settings/keys` | 免费模型 20 RPM / 50 次/天；充值 ≥$10 后提到 1000 次/天 | 官方明文：多 Key / 多账号**不扩容**。有 `qwen3-coder:free`、`nemotron-3-ultra:free` 等免费模型 |
| **硅基流动** | https://cloud.siliconflow.cn/ · Key `/account/ak` | 每日免费开源模型额度 | 免费层只有 9B 级小模型，旗舰全付费；限额固定不随消费提升。实测生成很慢，**只当最后兜底** |
| **火山方舟** | https://console.volcengine.com/ark | 新用户 50 万 tokens 体验包 | ⚠️ 需先在控制台**部署模型拿到 endpoint ID**（如 `ep-2024xxxx`）才能调用。额度按模型独立发放、用完即停，**一次性不恢复**。可加账号扩容（额度按账号独立） |
| **Cohere** | https://dashboard.cohere.com/ · Key `/api-keys` | Trial Key 全调用合计 1000 次/月；Chat 20 RPM | 额度太小，多注册无意义 |
| **HuggingFace** | Token https://huggingface.co/settings/tokens | 免费账号每月 $0.10 路由额度 | ≈ 可忽略，除非本来就有号 |

---

### 注册顺序建议

1. **先把 P0 这四个做完**，接进网关实测一周，看实际消耗
2. 再决定 P1 和 P2 —— 很多源接了之后根本用不到，**不必提前囤号**
3. 动手前先数一下手机号：需要实名的有 **阿里云百炼、魔搭（绑阿里云，不可逆）、国家超算**；
   需要手机验证的有 **NVIDIA NIM、商汤、阶跃星辰、Mistral**；其余只需邮箱

### 接入时的 5 个技术注意点

1. **模型名带 `/`**：NVIDIA 是 `deepseek-ai/deepseek-v4-flash`，魔搭是
   `Qwen/Qwen3.5-35B-A3B`。本网关用 `:` 和 `/` 做源路由分隔符，
   加 `model_map` 时**要实测确认不被误解析**（已长期验证：硅基流动的 `Qwen/Qwen3.5-27B`、魔搭的 `deepseek-ai/...` 都能正常路由；只有 `/` 前那一段恰好等于某个 provider 名时才会被误切，模型作者名不会撞）。
2. **需代理的源**（Gemini / Groq / Mistral / Cloudflare / OpenRouter）加
   `"proxy": "http://127.0.0.1:10808"`，只对该源生效，不影响国内源。
3. **魔搭要配 ≥10 个候选模型**，否则单模型上限会让它早早失效。
4. **国家超算 Key 创建时选"通用"服务类型**，不要买套餐（套餐 Key 接网关会被判按量计费扣钱）。
5. **同源多账号要用不同的 `name`**（如 `sensenova` / `sensenova2`）。
   冷却按 `name` 生效，同名会让一个账号 429 冻住全部。

> 入口地址来源：各平台官方站点与控制台（NVIDIA build.nvidia.com、ModelScope、
> 国家超算互联网 OpenAPI 文档、阿里云百炼控制台、商汤开放平台、阶跃星辰、AMD Radeon Cloud、
> 智谱开放平台、Google AI Studio、Groq、Cloudflare、Mistral、OpenRouter、硅基流动、
> 火山方舟、Cohere、HuggingFace）。
> 各平台控制台页面布局会调整，若链接失效请从官网首页进入。

---

## 配置说明

配置只有一份：`config/providers.json`（**不入库**，见 `.gitignore`）。

```jsonc
{
  "listen_host": "0.0.0.0",
  "listen_port": 8317,
  "default_model": "auto",
  "providers": [
    {
      "name": "your-provider",
      "enabled": true,
      "priority": 1,              // 越小越优先
      "base_url": "https://.../v1",
      "api_key": "REPLACE_ME",    // 单账号写法
      "models_out": ["model-a"],  // 该源对外暴露的模型名
      "model_map": { "auto": "model-a" },

      // 多账号写法：429/403 时在同源内自动换号
      "accounts": [
        { "name": "acct-1", "api_key": "REPLACE_ME", "max_concurrency": 4 }
      ],

      // 可选：AIMD 主动限速
      "rate_control": {
        "decrease": 0.85,
        "increase_step": 0.05,
        "recovery_seconds": 8
      },

      "cooldown": { "base": 60, "factor": 1.5, "max": 120, "jitter": 0.5 }
    }
  ]
}
```

几个关键概念：

- **`model_map`**：把对外的统一模型名（如 `auto`）映射到该源的真实模型名。值支持数组，用于源内多模型 fallback。
- **`accounts`**：同源多账号。额度按账号独立计算 —— **加账号才扩容，同账号多 key 无效**。
- **`cooldown`**：撞限流后的冷却时间，带指数退避与抖动，避免多账号同时恢复再次打爆。

---

## 客户端接入

任何 OpenAI 兼容客户端：

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8317/v1
export OPENAI_API_KEY=any-string      # 网关不校验，只认本地
# model 用 auto，或 /v1/models 里列出的具体名字
```

Claude Code / Anthropic 协议客户端走 `/v1/messages`，同样是 `base_url` 指过来即可。

内网其他机器（如私有组网）把 `127.0.0.1` 换成本机在内网的 IP。

---

## 网页控制台

启动后浏览器打开 **`http://127.0.0.1:8317/`** 即是控制台（无需额外进程、无需 Node/构建）。

它解决的是"改一个源要翻配置文件"的问题：

| 能力 | 说明 |
|---|---|
| **源状态总览** | 每个源一张卡片：启用状态、优先级、累计/24h token、实时成功失败数、最近失败原因 |
| **一键启停** | 卡片上直接停用/启用某个源，**立即写回 `config/providers.json`**，无需重启 |
| **优先级调整** | 卡片上直接改 priority，路由顺序即时生效 |
| **冷却重置** | 某源撞限流后想立刻恢复，点一下即可清空冷却与退避计数 |
| **连通性测试** | 对指定源发一次最小请求，返回真实可用性与延迟（不占用 `auto` 路由） |
| **账号池下钻** | 点卡片展开，看该源下每个账号的 Key（脱敏）、状态、并发、冷却、成功率 |
| **14 天用量图** | 按源堆叠柱状图 |
| **日志面板** | 在线看失败记录 / 用量明细 / 守护日志，不用去翻 `logs/` 目录 |

界面每 5 秒自动局部刷新（可关闭），刷新时不会打断正在进行的操作。

### 管理 API

控制台的每个操作都对应一个 HTTP 接口，也可以直接用脚本调用：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/providers` | `{action: enable\|disable\|set_priority\|reorder, provider, priority?, order?}` |
| POST | `/api/cooldown` | `{action: clear\|set, provider, seconds?, reason?}` |
| POST | `/api/test` | `{provider, model?}` 发一次真实请求做连通性测试 |
| POST | `/api/logs` | `{kind: usage\|fail\|supervisor\|router, lines?}` 尾读日志 |
| POST | `/api/reload` | 丢弃账号池，按最新配置重建 |

**鉴权**：默认只允许**回环地址**（`127.0.0.1` / `::1`）调用写接口。
如果通过内网 / Tailscale 暴露，需设置环境变量 `TOKENENGINE_UI_TOKEN`，
并在请求头带 `Authorization: Bearer <token>`；未设置时非回环请求一律 403。

> 写接口只改 `enabled` / `priority` 这两个字段，其余配置原样保留；
> 改动是原子的（临时文件 + `os.replace`），不会写出半个 JSON。

---

## 端点

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/` · `/dashboard` | **网页控制台** |
| GET | `/v1/models` | 可用模型列表 |
| POST | `/v1/chat/completions` | OpenAI Chat Completions |
| POST | `/v1/messages` | Anthropic Messages |
| POST | `/v1/responses` | OpenAI Responses API |
| GET | `/health` | 各源 / 各账号健康状态（含冷却剩余） |
| GET | `/api/metrics` | 运行指标，控制台数据源 |
| POST | `/api/*` | 控制台写操作（见上） |

---

## 目录结构

```
router.py            核心网关：协议解析、三层路由、转发、冷却
account_pool.py      账号池：账号状态、额度聚合、AIMD 限速器
dashboard.py         本地可视化看板
watchdog.py          进程保活
sentinel.py          各源健康度定期探测
supervisor_router.ps1 路由器守护（掉线自动拉起）
config/
  providers.example.json   配置模板（无密钥）
probe*.py / verify*.py     各源连通性与多模态能力探活
test_*.py                  回归与端到端测试
skills/                    配套能力（视觉识别、本地 OCR 等）
```

---

## 为什么不是又一个 OmniRoute / one-api

同类项目大致分两类：**做广度**（聚合上百家提供商，卖的是"一个 Key 调所有模型"）和
**做深度**（把单家账号池榨到极致）。TokenEngine 两条都不走，它解决的是第三个问题：

> 当你手里有十几家**免费额度**的源、它们的限流规则、上下文窗口、失效方式各不相同，
> 谁来负责"这一单该发给谁、失败了换谁、什么时候该放弃"？

这不是聚合问题，是**调度与限流语义**问题。具体差异：

| 维度 | 通用聚合网关（one-api / OmniRoute 这类） | TokenEngine |
|---|---|---|
| 目标 | 提供商越多越好，做统一入口 | 只收**国内可直连**的免费源，把额度用满、用稳 |
| 选源依据 | 手动指定 / 轮询 / 简单权重 | 按**抗压能力**（实测最大输入、成功率、429 率、超时率）排 priority |
| 上下文窗口 | 交给上游报错 | **路由器自己算**：估算输入 token，装不下的源直接跳过；全装不下就明确报错，而不是挨个白撞 400 |
| 限流处理 | 统一退避重试 | **区分语义**：`tpm/rpm` 限流 → 冷却后复用账号；`余额/资源包耗尽` → 长冷却；`凭据失效` → 才淘汰 |
| 空回复 | 直接透传给客户端 | **判为失败并补救**：`finish_reason=length` + 空内容 → 同源放宽 `max_tokens` 重试一次；仍空才切源 |
| 账号池 | 通常按提供商配 Key | 三层路由：**选源 → 选账号 → 选模型**，账号级 AIMD 主动限速 + 独立冷却 |
| 部署 | Docker / 数据库 / 前端构建 | **纯 Python 标准库**，无 Docker、无 DB、无 Node，双击即跑 |
| 观测 | 日志文件 | 内置**网页控制台**（`/`）：启停源、改优先级、清冷却、发真实探活、看账号池 |

一句话：**别人在解决"能不能连上"，TokenEngine 在解决"连上了之后怎么不浪费额度、不白等、不静默失败"。**

所以它不打算替代 one-api 这类通用聚合网关——如果你要的是"一个 Key 调 GPT/Claude/Gemini 全家桶"，
那些项目更合适；如果你要的是"把一堆免费额度调度得比手动切换更稳"，这才是这个项目的定位。

---

## 安全说明

- 本仓库**不含任何真实密钥**。配置模板 `config/providers.example.json` 全部为 `REPLACE_ME`。
- `config/providers.json` 已在 `.gitignore` 中排除，请勿提交。
- 探活脚本从**环境变量**读取密钥，不硬编码。
- 网关默认监听 `0.0.0.0`，**不要直接暴露到公网**；跨机器使用请走内网 / 私有组网。

---

## 免责声明

本项目仅用于**个人开发环境下的额度聚合**。请遵守各模型服务商的用户协议与使用条款，
不要用于规避官方计费或违反服务条款的场景。各源免费政策随时可能调整，以官方公告为准。
