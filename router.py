#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenEngine Router —— 永动机"发电中枢"。
OpenAI 兼容的本地转发网关：所有 CLI Agent 只连这里，背后挂多个免费源，自动轮询+故障切换。
纯标准库，免 pip。监听 0.0.0.0:8317，Tailscale 内其他机器/手机也能连。

路由规则：
- GET  /v1/models     返回可用模型列表
- POST /v1/chat/completions  选一个健康源转发；429/5xx/网络错自动切下一个
- 健康状态读 logs/token_pool.json（sentinel 写），不健康的源跳过
"""
import json, os, re, sys, time, uuid, urllib.request, urllib.error, threading, datetime, traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dashboard
import account_pool

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config", "providers.json")
POOL = os.path.join(HERE, "logs", "token_pool.json")
FAIL_LOG = os.path.join(HERE, "logs", "fail.log")

# pythonw（无窗口）下 sys.stdout/stderr 为 None，print 或未捕获异常会导致进程静默退出。
# 统一双写到日志文件（同时保留原控制台句柄），确保任何 traceback 都可追溯。
class _DualWriter:
    def __init__(self, console, path):
        self.console = console
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            self.fp = open(path, "a", encoding="utf-8")
        except Exception:
            self.fp = None
    def write(self, s):
        if self.fp is not None:
            try:
                self.fp.write(s); self.fp.flush()
            except Exception:
                pass
        if self.console is not None:
            try:
                self.console.write(s)
            except Exception:
                pass
    def flush(self):
        if self.fp is not None:
            try:
                self.fp.flush()
            except Exception:
                pass
        if self.console is not None:
            try:
                self.console.flush()
            except Exception:
                pass

sys.stdout = _DualWriter(sys.stdout, os.path.join(HERE, "logs", "router_out.log"))
sys.stderr = _DualWriter(sys.stderr, os.path.join(HERE, "logs", "router_err.log"))

# 实时状态（内存）：{provider: {ok, fail, last_ok, last_fail, last_err, last_model}}
LIVE = {}
_live_lock = threading.Lock()

def mark_ok(provider, model=None):
    with _live_lock:
        s = LIVE.setdefault(provider, {"ok": 0, "fail": 0})
        s["ok"] = s.get("ok", 0) + 1
        s["last_ok"] = datetime.datetime.now().isoformat(timespec="seconds")
        if model:
            s["last_model"] = model

def mark_fail(provider, err):
    rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
           "provider": provider, "err": str(err)[:300]}
    with _live_lock:
        s = LIVE.setdefault(provider, {"ok": 0, "fail": 0})
        s["fail"] = s.get("fail", 0) + 1
        s["last_fail"] = rec["ts"]; s["last_err"] = rec["err"]
    try:
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        with open(FAIL_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass

# ---------------- 内存熔断冷却（不耗额度的实时成败切换增强）----------------
# provider -> (冷却截止 epoch, 原因)。冷却期内 auto 路由直接跳过该源；显式 provider:model 不跳过。
COOLDOWN = {}
_cool_lock = threading.Lock()
# provider -> 连续限流次数（成功后清零），用于 429 指数退避
RATE_FAIL = {}

def cooldown_remaining(provider):
    """返回该源剩余冷却秒数；0 表示可用。"""
    with _cool_lock:
        v = COOLDOWN.get(provider)
        if not v:
            return 0
        until, _reason = v
        rem = until - time.time()
        if rem <= 0:
            COOLDOWN.pop(provider, None)
            return 0
        return int(rem) + 1

def set_cooldown(provider, seconds, reason):
    with _cool_lock:
        old = COOLDOWN.get(provider)
        until = max(time.time() + seconds, old[0] if old else 0)
        COOLDOWN[provider] = (until, reason)

def note_rate_limited(provider):
    """429 普通限流：连续次数指数退避 45s→90s→180s→300s 封顶。"""
    with _cool_lock:
        n = RATE_FAIL.get(provider, 0) + 1
        RATE_FAIL[provider] = n
    secs = min(45 * (2 ** (n - 1)), 300)
    set_cooldown(provider, secs, f"rate limited x{n}")
    return secs

def reset_rate_fail(provider):
    with _cool_lock:
        RATE_FAIL.pop(provider, None)

def cooldown_snapshot():
    """供 dashboard/health 展示当前冷却状态。"""
    now = time.time()
    with _cool_lock:
        out = {}
        for name, (until, reason) in COOLDOWN.items():
            rem = int(until - now)
            if rem > 0:
                out[name] = {"remaining": rem + 1, "reason": reason}
        return out

def cooldown_reason(provider):
    with _cool_lock:
        v = COOLDOWN.get(provider)
        return v[1] if v else ""

def estimate_input_tokens(messages, tools=None):
    """保守估算请求输入 token：中文约 1 字/token，其他约 3.5 字符/token，图片按固定开销。"""
    def tlen(s):
        if not isinstance(s, str):
            s = str(s)
        cjk = len(re.findall(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]", s))
        return cjk + (len(s) - cjk) / 3.5
    total = 0.0
    for m in messages or []:
        c = m.get("content")
        if isinstance(c, str):
            total += tlen(c)
        elif isinstance(c, list):
            for part in c:
                if not isinstance(part, dict):
                    continue
                if part.get("type") in ("text", "input_text", "output_text"):
                    total += tlen(part.get("text", ""))
                else:
                    total += 1200  # 图片/其他多模态 part 固定估算
        if m.get("tool_calls"):
            total += tlen(json.dumps(m["tool_calls"], ensure_ascii=False))
        total += 8
    if tools:
        total += tlen(json.dumps(tools, ensure_ascii=False))
    return int(total * 1.1) + 16

def _init_live():
    """启动时从 usage.log / fail.log 回填计数，使实时计数跨重启连续。"""
    try:
        with open(os.path.join(HERE, "logs", "usage.log"), "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                s = LIVE.setdefault(r.get("provider", "?"), {"ok": 0, "fail": 0})
                s["ok"] = s.get("ok", 0) + 1
                ts = r.get("ts", "")
                if ts and (not s.get("last_ok") or ts > s["last_ok"]):
                    s["last_ok"] = ts; s["last_model"] = r.get("model")
    except FileNotFoundError:
        pass
    try:
        with open(FAIL_LOG, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                s = LIVE.setdefault(r.get("provider", "?"), {"ok": 0, "fail": 0})
                s["fail"] = s.get("fail", 0) + 1
                ts = r.get("ts", "")
                if ts and (not s.get("last_fail") or ts > s["last_fail"]):
                    s["last_fail"] = ts; s["last_err"] = r.get("err", "")
    except FileNotFoundError:
        pass

def load_cfg():
    with open(CFG, "r", encoding="utf-8") as f:
        return json.load(f)


def save_cfg(cfg):
    """原子写回 providers.json；改配置后调它落盘。"""
    d = os.path.dirname(CFG)
    os.makedirs(d, exist_ok=True)
    tmp = CFG + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CFG)

def load_health():
    """返回 {provider_name: {ok:bool, detail:str, checked:str}}"""
    try:
        with open(POOL, "r", encoding="utf-8") as f:
            return json.load(f).get("providers", {})
    except Exception:
        return {}

def pools_snapshot_warm():
    """预热账号池后取快照，供控制台展示账号级状态（与 /health 同源）。"""
    try:
        for _p in load_cfg().get("providers", []):
            if _p.get("enabled", True):
                account_pool.get_pool(_p)
    except Exception:
        pass
    return account_pool.pools_snapshot()

def pick_providers(cfg):
    """按 priority 排序，跳过 disabled / 健康检查明确失败的。"""
    health = load_health()
    out = []
    for p in sorted(cfg["providers"], key=lambda x: x.get("priority", 99)):
        if not p.get("enabled", True):
            continue
        h = health.get(p["name"], {})
        if h.get("ok") is False:
            # 哨兵明确判死的源跳过（除非没有别的可用）
            continue
        out.append(p)
    if not out:  # 全被判死就退化为全量启用，避免全停
        out = [p for p in cfg["providers"] if p.get("enabled", True)]
    return out

def opener_for(p):
    """按源配置决定是否走代理。"""
    px = p.get("proxy")
    if px:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px}))
    return urllib.request.build_opener()

def extract_image_url(c):
    """从一个 content part 提取图片 URL，兼容 Responses input_image / OpenAI image_url / Anthropic base64 source。"""
    if not isinstance(c, dict):
        return None
    ty = c.get("type")
    iu = c.get("image_url")
    if isinstance(iu, str):
        return iu
    if isinstance(iu, dict) and iu.get("url"):
        return iu["url"]
    if c.get("url") and ty in ("input_image", "image"):
        return c["url"]
    src = c.get("source")
    if isinstance(src, dict) and src.get("data"):
        return "data:" + src.get("media_type", "image/png") + ";base64," + src["data"]
    if c.get("data") and ty in ("input_image", "image"):
        return "data:" + c.get("media_type", "image/png") + ";base64," + c["data"]
    return None

def _norm(s):
    """归一化名称用于 provider 匹配（忽略大小写与 - _ 空格 等符号）。"""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def candidate_models(p, want_model, force=False):
    """返回该 provider 对某逻辑模型名的上游候选模型列表。
    顺序：model_map 的 key 映射（可能是 list）→ 命中展平后的上游模型名 → force 原名 → auto 映射。"""
    mp = p.get("model_map", {})
    if want_model in mp:
        v = mp[want_model]
        return list(v) if isinstance(v, list) else [v]
    flat = set()
    for v in mp.values():
        if isinstance(v, list):
            flat.update(v)
        else:
            flat.add(v)
    if want_model in flat or force:
        return [want_model]
    v = mp.get("auto", want_model)
    return list(v) if isinstance(v, list) else [v]


def select_providers(providers, want_model):
    """统一选源，返回 (providers, real_want, is_explicit)。
    - "provider:model" 或 "provider/model"：精确指定源与模型（provider 名归一化/包含匹配）；
    - model_map 的 key（auto/vision/别名）或展平后的上游真实模型名：只路由到对应源；
    - auto 或未知名：返回全部源、非 explicit。
    """
    wm = str(want_model or "auto")
    hint = None
    for sep in (":", "/"):
        if sep in wm:
            a, b = wm.split(sep, 1)
            hint, wm = a.strip(), b.strip()
            break
    if hint:
        nh = _norm(hint)
        chosen = [p for p in providers if _norm(p["name"]) == nh or nh in _norm(p["name"])]
        if chosen:
            return chosen, wm, True, True
        # 前缀没匹配到真实源：模型名本身可能含 / 或 :（如 Qwen/Qwen3.5-27B），还原后按普通模型处理
        wm = str(want_model or "auto")
    if wm == "auto":
        return providers, wm, False, False

    def _has(p):
        mp = p.get("model_map", {})
        if wm in mp:
            return True
        for v in mp.values():
            if isinstance(v, list):
                if wm in v:
                    return True
            elif v == wm:
                return True
        return False

    ex = [p for p in providers if _has(p)]
    return (ex, wm, True, False) if ex else (providers, wm, False, False)

def _state_path():
    return os.path.join(HERE, "logs", "vision_model_state.json")

def state_start(provider, want_model, n):
    """该 provider+逻辑名上次成功的候选下标，默认 0。"""
    try:
        with open(_state_path(), encoding="utf-8") as f:
            i = json.load(f).get(provider, {}).get(want_model, 0)
        return i if isinstance(i, int) and 0 <= i < n else 0
    except Exception:
        return 0

def state_save(provider, want_model, i):
    try:
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        sp = _state_path()
        d = {}
        if os.path.exists(sp):
            with open(sp, encoding="utf-8") as f:
                d = json.load(f)
        d.setdefault(provider, {})[want_model] = i
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except Exception:
        pass

def should_failover(status_code, body):
    """该错误是否应在同源内换下一个候选模型（复刻 claude-vision-skill 的 shouldFailover）。"""
    if status_code in (429, 403, 404, 500, 502, 503):
        return True
    if status_code != 400:
        return False
    t = (body or "").lower()
    kws = ["quota", "limit", "throttl", "rate", "balance", "arrear", "allocation",
           "free", "exhaust", "expired", "not exist", "notfound", "invalidmodel",
           "unsupported", "access denied", "permission", "disabled",
           "must be <=", "must not exceed", "maximum context", "context length",
           "context window", "max input", "input length", "too long", "longer than",
           "max_new_tokens", "maximum number of tokens", "tokens +",
           "额度", "余额", "欠费", "耗尽", "到期", "未开通", "不存在", "不支持", "限流",
           "超长", "超出", "长度", "上下文", "最大"]
    return any(k in t for k in kws)

_ACCOUNT_WAIT = 3.0  # 同 provider 内等待可用账号的预算（秒）；超时即交给上层切源

def _try_provider(p, want_model, messages, tools=None, max_tokens=None, extra=None, force=False):
    """对单个 provider 按候选模型依次尝试（从上次成功下标开始绕一圈）。
    成功返回 (j, real_model, None)；全部失败返回 (None, None, last_err)。
    force=True（provider:model 显式指定）时候选直接用指定模型，不回退 auto。

    账号池改造（2026-09-27，借鉴 st-rotator）：provider 内若配了多个账号
    （accounts[]），遇 429/403 时先在同 provider 内换下一个账号，账号都试过才
    交给上层切下一个 provider。账号级冷却 + AIMD 主动限速由 account_pool 管理。
    """
    cands = candidate_models(p, want_model, force)
    if not cands:
        return None, None, f"{p['name']} 无候选模型（model_map 未配 {want_model}）"
    start = state_start(p["name"], want_model, len(cands))
    order = cands[start:] + cands[:start]
    timeout = int(p.get("timeout", 30))
    last_err = None

    pool = account_pool.get_pool(p)
    tried = set()
    rounds = pool.size if pool else 1

    for _ in range(max(1, rounds)):
        acct = None
        if pool:
            acct = pool.acquire(timeout=_ACCOUNT_WAIT, exclude=tried)
            if acct is None:
                # 本 provider 暂无可用账号（都在冷却/限速中），交给上层切源。
                # 必须写 last_err，否则上层会报出无信息量的 "all providers unavailable: None"。
                last_err = f"{p['name']} 无可用账号（全部冷却/限速中）"
                break
            use_key = acct.api_key
            tried.add(acct.name)
        else:
            use_key = p.get("api_key")
        if not use_key:
            break
        t0 = time.monotonic()
        switch_account = False
        try:
            for real_model in order:
                url = p["base_url"].rstrip("/") + "/chat/completions"
                payload = {"model": real_model, "messages": messages}
                if tools:
                    payload["tools"] = tools
                    payload["tool_choice"] = "auto"
                mt = max_tokens
                if mt:
                    if want_model == "vision" and mt > 1024:
                        mt = 1024
                    payload["max_tokens"] = mt
                if extra:
                    for k, v in extra.items():
                        if k not in payload:
                            payload[k] = v
                data = json.dumps(payload).encode("utf-8")
                rq = urllib.request.Request(url, data=data, method="POST")
                rq.add_header("Content-Type", "application/json")
                rq.add_header("Authorization", "Bearer " + use_key)
                op = opener_for(p)
                try:
                    with op.open(rq, timeout=timeout) as resp:
                        j = json.loads(resp.read().decode("utf-8"))
                    # 空内容防护：200 但既无文本也无工具调用（推理模型把 max_tokens 全耗在思考上时会出现），
                    # 必须判失败切源，否则客户端收到空白回复表现为"中断/没反应"。
                    ch0 = (j.get("choices") or [{}])[0]
                    msg0 = ch0.get("message", {}) or {}
                    content = msg0.get("content")
                    if (content is None or (isinstance(content, str) and not content.strip())) \
                            and not msg0.get("tool_calls"):
                        last_err = f"{p['name']} empty content (model={real_model}, finish={ch0.get('finish_reason')})"
                        log_line("FAIL " + last_err); mark_fail(p["name"], last_err)
                        set_cooldown(p["name"], 20, "empty content")
                        continue
                    state_save(p["name"], want_model, cands.index(real_model))
                    reset_rate_fail(p["name"])
                    if acct:
                        acct.on_success(time.monotonic() - t0)
                    return j, real_model, None
                except urllib.error.HTTPError as e:
                    body = e.read()[:300].decode("utf-8", "ignore")
                    last_err = f"{p['name']} HTTP {e.code} {body}"
                    log_line("FAIL " + last_err); mark_fail(p["name"], last_err)
                    low = body.lower()
                    if e.code == 429:
                        # 余额/资源包耗尽类长冷却；普通 tpm/rpm 限流按连续次数指数退避
                        if any(k in low for k in ("balance", "arrear", "充值", "余额", "资源包", "insufficient balance")):
                            set_cooldown(p["name"], 600, "balance/quota exhausted")
                            if acct:
                                acct.on_invalid(pool.invalid_ttl, reason="balance/quota exhausted")
                        else:
                            if acct:
                                cd = pool.cooldown_for(acct)
                                acct.on_rate_limited(cd)
                                log_line(f"ACCOUNT {p['name']}/{acct.name} cooldown {cd:.0f}s (429)")
                            secs = note_rate_limited(p["name"])
                            log_line(f"COOLDOWN {p['name']} {secs}s (rate limited)")
                        if not should_failover(e.code, body):
                            return None, None, last_err
                        switch_account = True   # 429：换下一个账号
                        break
                    elif e.code in (402, 403):
                        set_cooldown(p["name"], 300, f"HTTP {e.code}")
                        if acct:
                            acct.on_invalid(pool.invalid_ttl, reason=f"HTTP {e.code}")
                        if not should_failover(e.code, body):
                            return None, None, last_err
                        switch_account = True   # 凭据失效：换账号
                        break
                    if not should_failover(e.code, body):
                        return None, None, last_err
                    # 其余可切换错误（400/404 等）：换同账号的下一个候选模型
                except Exception as e:
                    last_err = f"{p['name']} {type(e).__name__} {e}"
                    log_line("FAIL " + last_err); mark_fail(p["name"], last_err)
                    # 超时/连接错短冷却，避免后续请求继续在慢源上串行等待
                    set_cooldown(p["name"], 15, f"{type(e).__name__}")
                    if acct:
                        acct.on_server_error(pool.server_error_cooldown, reason=type(e).__name__)
                    switch_account = True
                    break
        finally:
            if acct:
                pool.release(acct)
        if not switch_account:
            break
    return None, None, last_err

def _first_image_data_url(messages):
    for m in messages:
        c = m.get("content")
        if isinstance(c, list):
            for part in c:
                u = extract_image_url(part)
                if u:
                    return u
    return None

def local_ocr_answer(messages):
    """视觉云源全失败后的本地 OCR 兜底，返回 OpenAI chat 形态响应；不可用返回 None。"""
    try:
        import local_ocr_engine
        if not local_ocr_engine.available():
            return None
        u = _first_image_data_url(messages)
        if not u:
            return None
        text = local_ocr_engine.ocr_data_url(u)
        if not text:
            return None
        content = "【本地OCR兜底·离线识别，仅含图中文字、不做语义理解】\n" + text
        return {"id": "chat_localocr", "object": "chat.completion", "created": int(time.time()),
                "model": "rapidocr-local",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}}
    except Exception as e:
        log_line("local ocr fallback failed: " + str(e))
        return None

def _unwrap_tool_args(raw):
    """剥掉上游模型误加的一层或多层 {"arguments": {...}} 包装。

    背景：部分弱模型（视觉/实验模型）在长上下文 + 多工具场景下，会把 tool_call 的
    arguments 写成 {"arguments": {"cmd": "..."}} 甚至嵌套两层，导致客户端（如 Codex）
    始终解析不到 cmd 字段。最多剥 3 层；解析失败或结构不符则原样返回。
    """
    if not isinstance(raw, str) or not raw.strip():
        return raw
    try:
        o = json.loads(raw)
    except Exception:
        return raw
    changed = False
    for _ in range(3):
        if not (isinstance(o, dict) and set(o.keys()) == {"arguments"}):
            break
        inner = o["arguments"]
        if isinstance(inner, str):
            try:
                inner = json.loads(inner)
            except Exception:
                break
        if not isinstance(inner, dict):
            break
        o = inner
        changed = True
    if not changed:
        return raw
    try:
        return json.dumps(o, ensure_ascii=False)
    except Exception:
        return raw

def _sanitize_tool_args(j, tools=None):
    """就地清洗响应里 tool_calls 的 arguments（防御上游模型的格式漂移）。

    仅在「工具 schema 本身不含 arguments 属性」时才解包，避免误伤合法参数。
    """
    try:
        allow = {}
        for t in (tools or []):
            fn = t.get("function") or {}
            props = ((fn.get("parameters") or {}).get("properties") or {})
            allow[fn.get("name")] = "arguments" in props
        for ch in (j.get("choices") or []):
            msg = ch.get("message") or {}
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function") or {}
                raw = fn.get("arguments")
                if not isinstance(raw, str) or allow.get(fn.get("name")):
                    continue
                fixed = _unwrap_tool_args(raw)
                if fixed != raw:
                    fn["arguments"] = fixed
    except Exception:
        pass

def chat_call(messages, tools=None, model="auto", max_tokens=None, require_explicit=False, extra=None):
    """模块级核心：按 priority 发给免费源；支持单源多模型 fallback，视觉全失败走本地 OCR。
    require_explicit=True 时只走 model_map 显式含该模型名的源（用于视觉等特定模型）。
    返回 (response_dict, provider_name, last_err)。成功时 err=None。"""
    allp = pick_providers(load_cfg())
    providers, real_want, is_explicit, force = select_providers(allp, model)
    if require_explicit and not is_explicit:
        # 自动检测到图片但未显式指定：只走配了 vision 的源
        vis = [p for p in providers if "vision" in p.get("model_map", {})]
        if vis:
            providers, real_want = vis, "vision"
    is_vision = real_want == "vision" or model == "vision"
    est = estimate_input_tokens(messages, tools) + int(max_tokens or 0)
    skipped = []

    # 上下文窗口感知：跳过窗口装不下本次请求的源（如 bigmodel 16k 上限）
    # 注意：vision 请求也必须过滤。曾经这里写成 `not is_vision`，导致「长对话 + 截图」的请求
    # 会把几万 token 直接砸进 16k 窗口的 bigmodel，每次白撞一次 400 并多等一轮往返。
    # est 里图片只按固定 1200 token 计，所以正常识图请求不会被误杀。
    if not force:
        fitted = []
        for p in providers:
            cw = p.get("context_window")
            if cw and est > cw:
                skipped.append(f"{p['name']}(ctx {cw}<~{est})")
            else:
                fitted.append(p)
        if fitted:
            providers = fitted

    # 熔断冷却：auto 路由跳过冷却中的源；显式 provider:model 仍尊重用户指定
    if not force:
        ready, cooling = [], []
        for p in providers:
            rem = cooldown_remaining(p["name"])
            if rem > 0:
                cooling.append((p, rem))
            else:
                ready.append(p)
        if ready:
            providers = ready
            for p, rem in cooling:
                skipped.append(f"{p['name']}(cool {rem}s)")
        elif cooling:
            cooling.sort(key=lambda x: x[1])
            providers = [cooling[0][0]]
            log_line(f"all providers cooling, fallback to {providers[0]['name']}")
    if skipped:
        log_line(f"route ~{est}tok skip: {' '.join(skipped)}")

    last_err = None
    for p in providers:
        j, real_model, err = _try_provider(p, real_want, messages, tools, max_tokens,
                                           extra=extra, force=force)
        if j is not None:
            _sanitize_tool_args(j, tools)
            record_usage(p["name"], real_model, j)
            return j, p["name"], None
        last_err = err
    if is_vision:
        j = local_ocr_answer(messages)
        if j is not None:
            record_usage("local-ocr", "rapidocr-local", j)
            return j, "local-ocr", None
    return None, None, last_err

def log_line(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)

def record_usage(provider, model, j):
    """把每次成功请求的 token 用量追加到 logs/usage.log（JSONL）。任何失败不影响主链路。"""
    try:
        u = j.get("usage", {}) or {}
        rec = {
            "ts": datetime.datetime.now().isoformat(timespec="seconds"),
            "provider": provider,
            "model": j.get("model") or model,
            "in": u.get("prompt_tokens", 0),
            "out": u.get("completion_tokens", 0),
            "total": u.get("total_tokens", 0),
        }
        rc = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
        if rc:
            rec["reasoning"] = rc
        os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
        with open(os.path.join(HERE, "logs", "usage.log"), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        mark_ok(provider, rec.get("model"))
    except Exception:
        pass

class Handler(BaseHTTPRequestHandler):
    def _send_json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/dashboard"):
            m = dashboard.build_metrics(HERE, LIVE, cooldown_snapshot(), pools_snapshot_warm())
            data = dashboard.render_html(m).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        if path == "/api/metrics":
            m = dashboard.build_metrics(HERE, LIVE, cooldown_snapshot(), pools_snapshot_warm())
            m["now"] = m["now"].isoformat(timespec="seconds")
            for p in m["providers"]:
                if p["stat"].get("last"):
                    p["stat"]["last"] = p["stat"]["last"].isoformat(timespec="seconds")
                p["stat"]["models"] = sorted(p["stat"]["models"])
            return self._send_json(200, m)
        if self.path.startswith("/v1/models") or self.path == "/models":
            cfg = load_cfg()
            ids = sorted({m for p in cfg["providers"] if p.get("enabled") for m in p.get("models_out", [])})
            ids.append(cfg.get("default_model", "auto"))
            return self._send_json(200, {"object": "list",
                "data": [{"id": i, "object": "model", "created": 0, "owned_by": "tokenengine"} for i in ids]})
        if self.path.startswith("/health"):
            # 预热所有启用 provider 的账号池，便于 /health 一次性看到全部账号状态
            try:
                for _p in load_cfg().get("providers", []):
                    if _p.get("enabled", True):
                        account_pool.get_pool(_p)
            except Exception:
                pass
            return self._send_json(200, {"ok": True, "ts": time.time(),
                                         "pool": load_health(),
                                         "cooldown": cooldown_snapshot(),
                                         "accounts": account_pool.pools_snapshot()})
        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        try:
            if self.path.startswith("/api/"):
                return self._admin_post()
            if self.path.startswith("/v1/chat/completions"):
                return self._openai_chat()
            if self.path.startswith("/v1/messages"):
                return self._anthropic_messages()
            if self.path.startswith("/v1/responses"):
                return self._responses_endpoint()
            self._send_json(404, {"error": "not found"})
        except Exception as e:
            log_line("handler error: " + traceback.format_exc())
            try:
                self._send_json(500, {"error": {"message": f"router internal error: {e}",
                                                "type": "router_error"}})
            except Exception:
                pass

    # ---------------- 管理台 /api/* ----------------
    def _ui_allowed(self):
        """回环地址免鉴权；非回环需 bearer token（TOKENENGINE_UI_TOKEN）。"""
        peer = (self.client_address[0] or "") if self.client_address else ""
        if peer in ("127.0.0.1", "::1", "localhost") or peer.startswith("::ffff:127."):
            return True
        tok = os.environ.get("TOKENENGINE_UI_TOKEN", "")
        if not tok:
            return False
        return self.headers.get("Authorization", "") == "Bearer " + tok

    def _read_body_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8", "replace"))

    def _admin_post(self):
        if not self._ui_allowed():
            return self._send_json(403, {"error": "admin api requires loopback client or TOKENENGINE_UI_TOKEN"})
        path = self.path.split("?", 1)[0]
        body = self._read_body_json()

        if path == "/api/providers":
            return self._admin_providers(body)
        if path == "/api/cooldown":
            return self._admin_cooldown(body)
        if path == "/api/test":
            return self._admin_test(body)
        if path == "/api/logs":
            return self._admin_logs(body)
        if path == "/api/reload":
            cfg = load_cfg()
            account_pool.drop_all_pools()
            return self._send_json(200, {"ok": True, "action": "reload",
                                         "providers": [p["name"] for p in cfg.get("providers", [])]})
        return self._send_json(404, {"error": "not found"})

    def _admin_providers(self, body):
        """动作：enable / disable / set_priority / reorder。立即落盘并对后续请求生效。"""
        action = body.get("action")
        cfg = load_cfg()
        provs = cfg.get("providers", [])
        by_name = {p.get("name"): p for p in provs}

        if action in ("enable", "disable"):
            name = body.get("provider")
            if name not in by_name:
                return self._send_json(404, {"error": f"unknown provider: {name}"})
            by_name[name]["enabled"] = (action == "enable")
            save_cfg(cfg)
            account_pool.drop_pool(name)
            return self._send_json(200, {"ok": True, "provider": name,
                                         "enabled": by_name[name]["enabled"]})

        if action == "set_priority":
            name = body.get("provider")
            prio = body.get("priority")
            if name not in by_name:
                return self._send_json(404, {"error": f"unknown provider: {name}"})
            if not isinstance(prio, int) or prio < 1:
                return self._send_json(400, {"error": "priority must be a positive integer"})
            by_name[name]["priority"] = prio
            save_cfg(cfg)
            return self._send_json(200, {"ok": True, "provider": name, "priority": prio})

        if action == "reorder":
            order = body.get("order") or []
            missing = [n for n in order if n not in by_name]
            if missing:
                return self._send_json(400, {"error": f"unknown providers in order: {missing}"})
            for i, n in enumerate(order, start=1):
                by_name[n]["priority"] = i
            save_cfg(cfg)
            return self._send_json(200, {"ok": True, "action": "reorder", "order": order})

        return self._send_json(400, {"error": f"unsupported action: {action}"})

    def _admin_cooldown(self, body):
        """动作：clear（清冷却）/ set（手动设冷却，秒）。"""
        action = body.get("action")
        name = body.get("provider")
        cfg = load_cfg()
        names = {p.get("name") for p in cfg.get("providers", [])}
        if name not in names:
            return self._send_json(404, {"error": f"unknown provider: {name}"})
        if action == "clear":
            with _cool_lock:
                COOLDOWN.pop(name, None)
            reset_rate_fail(name)
            account_pool.clear_cooldown(name)
            return self._send_json(200, {"ok": True, "provider": name, "action": "clear"})
        if action == "set":
            secs = body.get("seconds")
            if not isinstance(secs, (int, float)) or secs <= 0:
                return self._send_json(400, {"error": "seconds must be > 0"})
            set_cooldown(name, int(secs), body.get("reason", "manual"))
            return self._send_json(200, {"ok": True, "provider": name, "seconds": secs})
        return self._send_json(400, {"error": f"unsupported action: {action}"})

    def _admin_test(self, body):
        """对单个 provider 发一次最小请求，返回真实可用性与延迟。不占用 auto 路由。"""
        name = body.get("provider")
        cfg = load_cfg()
        target = next((p for p in cfg.get("providers", []) if p.get("name") == name), None)
        if not target:
            return self._send_json(404, {"error": f"unknown provider: {name}"})
        models = body.get("model") or target.get("models_out") or ["auto"]
        if isinstance(models, str):
            models = [models]
        probe_msgs = [{"role": "user", "content": "ping"}]
        results = []
        for m in models[:3]:
            t0 = time.time()
            try:
                j, prov, err = _try_provider(dict(target), m, probe_msgs, force=True)
                lat = int((time.time() - t0) * 1000)
                if j is not None:
                    txt = ""
                    try:
                        txt = (j["choices"][0]["message"]["content"] or "")[:40]
                    except Exception:
                        pass
                    results.append({"model": m, "ok": True, "latency_ms": lat, "sample": txt})
                else:
                    results.append({"model": m, "ok": False, "latency_ms": lat,
                                    "error": str(err)[:160]})
            except Exception as e:
                results.append({"model": m, "ok": False,
                                "latency_ms": int((time.time() - t0) * 1000),
                                "error": f"{type(e).__name__}: {e}"[:160]})
        return self._send_json(200, {"ok": any(r["ok"] for r in results),
                                     "provider": name, "results": results})

    def _admin_logs(self, body):
        """尾读日志。kind: usage | fail | supervisor | shim；返回结构化行。"""
        kind = body.get("kind", "fail")
        n = max(1, min(int(body.get("lines", 50) or 50), 500))
        files = {
            "usage": os.path.join(HERE, "logs", "usage.log"),
            "fail": FAIL_LOG,
            "supervisor": os.path.join(HERE, "logs", "supervisor.log"),
            "router": os.path.join(HERE, "logs", "router.log"),
        }
        fp = files.get(kind)
        if not fp:
            return self._send_json(400, {"error": f"unknown kind: {kind}", "supported": sorted(files)})
        if not os.path.exists(fp):
            return self._send_json(200, {"kind": kind, "path": fp, "exists": False, "lines": []})
        out = []
        try:
            with open(fp, "rb") as f:
                size = f.seek(0, 2)
                f.seek(max(0, size - 512 * 1024))
                chunk = f.read().decode("utf-8", "replace")
            out = chunk.splitlines()[-n:]
        except Exception as e:
            return self._send_json(500, {"error": f"read failed: {e}"})
        parsed = []
        for ln in out:
            try:
                parsed.append(json.loads(ln))
            except Exception:
                parsed.append({"raw": ln[:400]})
        return self._send_json(200, {"kind": kind, "path": fp, "exists": True,
                                     "count": len(parsed), "lines": parsed})

    def _capture_responses(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b""
        fn = ""
        try:
            os.makedirs(os.path.join(HERE, "logs"), exist_ok=True)
            ts = datetime.datetime.now().strftime("%H%M%S_%f")
            fn = os.path.join(HERE, "logs", "resp_" + ts + ".json")
            with open(fn, "wb") as f:
                f.write(body)
        except Exception as e:
            log_line("capture error " + str(e))
        return self._send_json(501, {"error": "captured to " + os.path.basename(fn)})

    def _forward(self, openai_payload):
        """统一选源转发 OpenAI payload（复用 chat_call 的冷却/上下文/视觉逻辑），返回 (status, out_bytes, provider_name)。"""
        j, prov, err = chat_call(openai_payload.get("messages", []),
                                 tools=openai_payload.get("tools"),
                                 model=openai_payload.get("model", "auto"),
                                 max_tokens=openai_payload.get("max_tokens"))
        if j is None:
            body = {"error": {"message": f"all providers unavailable: {err}",
                              "type": "upstream_unavailable"}}
            return 503, json.dumps(body, ensure_ascii=False).encode(), None
        return 200, json.dumps(j, ensure_ascii=False).encode(), prov

    def _anthropic_messages(self):
        """Anthropic Messages API → OpenAI 转发 → 回包翻回 Anthropic。

        支持：文本 / 图片 / **工具调用（tool_use + tool_result）** / 流式 SSE。
        工具调用是 Claude Code 的核心能力——缺了它 CC 只能"说"不能"做"
        （表现为 "The model's tool call could not be parsed"）。
        """
        length = int(self.headers.get("Content-Length", 0))
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return self._send_json(400, {"error": "bad json"})

        msgs = []
        if req.get("system"):
            sc = req["system"]
            if isinstance(sc, list):
                sc = "".join(b.get("text", "") for b in sc if isinstance(b, dict))
            msgs.append({"role": "system", "content": sc})

        for m in req.get("messages", []):
            role = m.get("role", "user")
            c = m.get("content", "")
            if isinstance(c, str):
                msgs.append({"role": role, "content": c})
                continue
            if not isinstance(c, list):
                msgs.append({"role": role, "content": str(c)})
                continue
            text_parts, tool_uses, tool_results = [], [], []
            for b in c:
                if not isinstance(b, dict):
                    continue
                t = b.get("type")
                if t == "text":
                    text_parts.append(b.get("text", ""))
                elif t == "image":
                    src_ = b.get("source", {}) or {}
                    if src_.get("type") == "base64" and src_.get("data"):
                        text_parts.append({"type": "image_url", "image_url": {
                            "url": "data:%s;base64,%s" % (
                                src_.get("media_type", "image/png"), src_["data"])}})
                elif t == "tool_use":
                    tool_uses.append({
                        "id": b.get("id") or ("call_" + uuid.uuid4().hex[:16]),
                        "type": "function",
                        "function": {"name": b.get("name", ""),
                                     "arguments": json.dumps(b.get("input", {}), ensure_ascii=False)}})
                elif t == "tool_result":
                    cont = b.get("content")
                    if isinstance(cont, list):
                        cont = "".join(x.get("text", "") for x in cont if isinstance(x, dict))
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": b.get("tool_use_id") or "",
                        "content": cont if isinstance(cont, str)
                                   else json.dumps(cont, ensure_ascii=False)})
            if role == "assistant" and tool_uses:
                txt = "".join(p for p in text_parts if isinstance(p, str))
                msgs.append({"role": "assistant", "content": txt or None, "tool_calls": tool_uses})
            elif tool_results:
                msgs.extend(tool_results)
            else:
                content = text_parts if any(isinstance(p, dict) for p in text_parts) else "".join(text_parts)
                msgs.append({"role": role, "content": content})

        oa = {"model": req.get("model", "auto"), "messages": msgs,
              "max_tokens": req.get("max_tokens", 1024)}
        # 工具定义：Anthropic input_schema → OpenAI parameters
        if req.get("tools"):
            oa["tools"] = [{"type": "function",
                            "function": {"name": t.get("name", ""),
                                         "description": t.get("description", ""),
                                         "parameters": t.get("input_schema")
                                                       or {"type": "object", "properties": {}}}}
                           for t in req["tools"] if isinstance(t, dict) and t.get("name")]
            tc = req.get("tool_choice") or {}
            tt = tc.get("type") if isinstance(tc, dict) else None
            if tt == "any":
                oa["tool_choice"] = "required"
            elif tt == "tool" and tc.get("name"):
                oa["tool_choice"] = {"type": "function", "function": {"name": tc["name"]}}
            else:
                oa["tool_choice"] = "auto"

        status, out, prov = self._forward(oa)
        if status != 200:
            try:
                return self._send_json(status, json.loads(out.decode("utf-8", "ignore")))
            except Exception:
                return self._send_json(status, {"error": "upstream error"})

        try:
            j = json.loads(out)
            record_usage(prov, req.get("model", "auto"), j)
            ch0 = (j.get("choices") or [{}])[0]
            msg0 = ch0.get("message", {}) or {}
            txt = msg0.get("content") or ""
            tool_calls = msg0.get("tool_calls") or []
            finish = ch0.get("finish_reason", "stop")
            stop_map = {"stop": "end_turn", "length": "max_tokens"}
            stop_reason = "tool_use" if tool_calls else stop_map.get(finish, "end_turn")
            raw_id = str(j.get("id") or uuid.uuid4().hex)
            msg_id = raw_id if raw_id.startswith("msg_") else "msg_" + raw_id[:24]
            u = j.get("usage", {}) or {}
            in_tok = u.get("prompt_tokens", 0)
            out_tok = u.get("completion_tokens", 0)
            model = req.get("model", "auto")

            blocks = []
            if txt:
                blocks.append({"type": "text", "text": txt})
            for tc in tool_calls:
                fn = tc.get("function", {}) or {}
                try:
                    inp = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    inp = {"_raw": fn.get("arguments")}
                blocks.append({"type": "tool_use",
                               "id": tc.get("id") or ("toolu_" + uuid.uuid4().hex[:24]),
                               "name": fn.get("name", ""), "input": inp})
            if not blocks:
                blocks.append({"type": "text", "text": ""})

            if req.get("stream"):
                return self._write_anthropic_sse(msg_id, model, blocks, stop_reason,
                                                 in_tok, out_tok, prov)

            resp = {"id": msg_id, "type": "message", "role": "assistant", "model": model,
                    "content": blocks, "stop_reason": stop_reason, "stop_sequence": None,
                    "usage": {"input_tokens": in_tok, "output_tokens": out_tok}}
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("x-tokenengine-provider", prov or "")
            self.end_headers()
            self.wfile.write(json.dumps(resp, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self._send_json(502, {"error": "translate failed: %s" % e})

    def _write_anthropic_sse(self, msg_id, model, blocks, stop_reason, in_tok, out_tok, prov):
        """Anthropic Messages SSE，支持 text 与 tool_use 两种 content block。"""
        buf = b""
        buf += self._sse("message_start", {
            "type": "message_start",
            "message": {"id": msg_id, "type": "message", "role": "assistant",
                        "model": model, "content": [], "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": in_tok, "output_tokens": 0}}})
        for i, blk in enumerate(blocks):
            if blk.get("type") == "text":
                buf += self._sse("content_block_start", {
                    "type": "content_block_start", "index": i,
                    "content_block": {"type": "text", "text": ""}})
                if blk.get("text"):
                    buf += self._sse("content_block_delta", {
                        "type": "content_block_delta", "index": i,
                        "delta": {"type": "text_delta", "text": blk["text"]}})
                buf += self._sse("content_block_stop", {"type": "content_block_stop", "index": i})
            elif blk.get("type") == "tool_use":
                buf += self._sse("content_block_start", {
                    "type": "content_block_start", "index": i,
                    "content_block": {"type": "tool_use", "id": blk.get("id"),
                                      "name": blk.get("name"), "input": {}}})
                buf += self._sse("content_block_delta", {
                    "type": "content_block_delta", "index": i,
                    "delta": {"type": "input_json_delta",
                              "partial_json": json.dumps(blk.get("input", {}), ensure_ascii=False)}})
                buf += self._sse("content_block_stop", {"type": "content_block_stop", "index": i})
        buf += self._sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason, "stop_sequence": None},
            "usage": {"output_tokens": out_tok}})
        buf += self._sse("message_stop", {"type": "message_stop"})

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("x-tokenengine-provider", prov or "")
        self.end_headers()
        self.wfile.write(buf)
        self.close_connection = True

    def _openai_chat(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            return self._send_json(400, {"error": "bad json"})

        want_stream = bool(req.get("stream"))
        want_model = req.get("model", "auto")
        messages = req.get("messages", [])
        # 含图片且未显式指定源/模型时，走 vision 专用源
        has_image = any(isinstance(m.get("content"), list) and
                        any(isinstance(part, dict) and extract_image_url(part)
                            for part in m["content"])
                        for m in messages if isinstance(m.get("content"), list))
        _, _, model_explicit, _ = select_providers(pick_providers(load_cfg()), want_model)
        use_model, require_explicit = want_model, False
        if has_image and not model_explicit:
            use_model, require_explicit = "vision", True
        extra = {"temperature": req["temperature"]} if "temperature" in req else None

        j, prov, err = chat_call(messages, tools=req.get("tools"), model=use_model,
                                 max_tokens=req.get("max_tokens"),
                                 require_explicit=require_explicit, extra=extra)
        if j is None:
            return self._send_json(503, {"error": {"message": f"all providers unavailable: {err}",
                                                   "type": "upstream_unavailable"}})

        def finish(jj, pp, real_model):
            log_line(f"OK {pp} model={real_model} stream={want_stream}")
            if want_stream:
                return self._write_chat_sse(jj, pp, real_model)
            data2 = json.dumps(jj, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data2)))
            self.send_header("Connection", "close")
            self.send_header("x-tokenengine-provider", pp or "")
            self.send_header("x-tokenengine-model", real_model or "")
            self.end_headers()
            self.wfile.write(data2)
            self.close_connection = True

        return finish(j, prov, j.get("model"))

    def _write_chat_sse(self, j, prov, real_model):
        """把非流式 OpenAI chat 响应 j 转成标准 chat.completion.chunk SSE（一次性 delta），写完即关连接。"""
        cid = j.get("id", "chat_")
        model = j.get("model", "auto")
        created = j.get("created", 0)
        ch0 = (j.get("choices") or [{}])[0]
        msg = ch0.get("message", {})
        finish = ch0.get("finish_reason", "stop")
        idx = ch0.get("index", 0)

        def chunk(delta, fin):
            o = {"id": cid, "object": "chat.completion.chunk", "created": created,
                 "model": model, "choices": [{"index": idx, "delta": delta, "finish_reason": fin}]}
            return ("data: " + json.dumps(o, ensure_ascii=False) + "\n\n").encode("utf-8")

        buf = chunk({"role": "assistant", "content": ""}, None)
        # 思考模式：reasoning_content 必须透传。否则客户端拿不到推理内容、下一轮无法回传，
        # 上游会返回 400 "the reasoning content from the previous turn must be passed back in thinking mode"。
        if msg.get("reasoning_content"):
            buf += chunk({"reasoning_content": msg["reasoning_content"]}, None)
        if msg.get("content"):
            buf += chunk({"content": msg["content"]}, None)
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            buf += chunk({"tool_calls": [{"index": i, "id": tc.get("id"), "type": "function",
                       "function": {"name": fn.get("name", ""), "arguments": fn.get("arguments", "")}}]}, None)
        buf += chunk({}, finish)
        buf += b"data: [DONE]\n\n"

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("x-tokenengine-provider", prov or "")
        self.send_header("x-tokenengine-model", real_model or "")
        self.end_headers()
        self.wfile.write(buf)
        self.close_connection = True

    # ---------------- Responses API（Codex 0.156+，流式 SSE）----------------
    def _sse(self, event_type, obj):
        return f"event: {event_type}\ndata: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")

    def _responses_endpoint(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        try:
            with open(os.path.join(HERE, "logs", "last_responses_req.json"), "wb") as f:
                f.write(raw)
        except Exception:
            pass
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            return self._send_json(400, {"error": "bad json"})

        # instructions + input -> chat messages
        messages = []
        if req.get("instructions"):
            messages.append({"role": "system", "content": req["instructions"]})
        messages += self._resp_input_to_chat(req.get("input"))
        chat_tools = self._resp_tools_to_chat(req.get("tools"))
        mt = req.get("max_output_tokens") or 2048

        # 含图片则标记 has_image。若客户端显式指定了源/模型则尊重，否则图片走 vision、纯文本走 auto。
        # 注意：只检查「最后一条 user 消息」。若遍历全部历史，只要会话里出现过一张截图，
        # 后续所有请求（包括纯文本的 coding 任务）都会被永久锁死在 vision 路由上；
        # 而视觉模型的工具调用能力通常远弱于文本模型，会导致 tool arguments 结构漂移。
        last_user = next((m for m in reversed(messages) if m.get("role") == "user"), None)
        has_image = bool(last_user and isinstance(last_user.get("content"), list) and
                         any(isinstance(p, dict) and p.get("type") == "image_url"
                             for p in last_user["content"]))
        req_model = req.get("model") or "auto"
        _, _, model_explicit, _ = select_providers(pick_providers(load_cfg()), req_model)
        if model_explicit:
            use_model, require_explicit = req_model, False
        elif chat_tools:
            # 带工具 = Agent 场景：工具调用能力优先于视觉能力，走文本路由
            use_model, require_explicit = "auto", False
        else:
            use_model = "vision" if has_image else "auto"
            require_explicit = has_image
        j, prov, err = chat_call(messages, tools=chat_tools or None,
                                model=use_model, max_tokens=mt, require_explicit=require_explicit)
        if j is None:
            return self._send_json(503, {"error": {"message": f"all providers unavailable: {err}",
                                                   "type": "upstream_unavailable"}})

        msg0 = (j.get("choices") or [{}])[0].get("message", {})
        text = msg0.get("content") or ""
        tool_calls = msg0.get("tool_calls") or []
        usage = j.get("usage", {})

        rid = "resp_" + uuid.uuid4().hex[:24]
        req_model = req.get("model") or "auto"
        created = int(time.time())
        base = {"id": rid, "object": "response", "created_at": created, "status": "in_progress",
                "model": req_model, "output": [], "usage": None, "store": False}
        events = [("response.created", {"type": "response.created", "response": dict(base)}),
                  ("response.in_progress", {"type": "response.in_progress", "response": dict(base)})]
        out_items = []
        idx = 0

        if text:
            mid = "msg_" + uuid.uuid4().hex[:24]
            events.append(("response.output_item.added", {"type": "response.output_item.added", "output_index": idx,
                "item": {"type": "message", "id": mid, "role": "assistant", "status": "in_progress", "content": []}}))
            events.append(("response.content_part.added", {"type": "response.content_part.added", "item_id": mid,
                "output_index": idx, "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}}))
            events.append(("response.output_text.delta", {"type": "response.output_text.delta", "item_id": mid,
                "output_index": idx, "content_index": 0, "delta": text}))
            events.append(("response.output_text.done", {"type": "response.output_text.done", "item_id": mid,
                "output_index": idx, "content_index": 0, "text": text}))
            part = {"type": "output_text", "text": text, "annotations": []}
            events.append(("response.content_part.done", {"type": "response.content_part.done", "item_id": mid,
                "output_index": idx, "content_index": 0, "part": part}))
            item = {"type": "message", "id": mid, "role": "assistant", "status": "completed", "content": [part]}
            events.append(("response.output_item.done", {"type": "response.output_item.done", "output_index": idx, "item": item}))
            out_items.append(item)
            idx += 1

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            args = fn.get("arguments", "")
            cid = tc.get("id") or ("call_" + uuid.uuid4().hex[:24])
            events.append(("response.output_item.added", {"type": "response.output_item.added", "output_index": idx,
                "item": {"type": "function_call", "id": cid, "call_id": cid, "name": name, "arguments": "", "status": "in_progress"}}))
            events.append(("response.function_call_arguments.delta", {"type": "response.function_call_arguments.delta",
                "item_id": cid, "output_index": idx, "delta": args}))
            events.append(("response.function_call_arguments.done", {"type": "response.function_call_arguments.done",
                "item_id": cid, "output_index": idx, "arguments": args}))
            item = {"type": "function_call", "id": cid, "call_id": cid, "name": name, "arguments": args, "status": "completed"}
            events.append(("response.output_item.done", {"type": "response.output_item.done", "output_index": idx, "item": item}))
            out_items.append(item)
            idx += 1

        final = {"id": rid, "object": "response", "created_at": created, "status": "completed", "model": req_model,
                 "output": out_items, "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                 "output_tokens": usage.get("completion_tokens", 0), "total_tokens": usage.get("total_tokens", 0)}}
        events.append(("response.completed", {"type": "response.completed", "response": final}))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("x-tokenengine-provider", prov or "")
        self.send_header("x-tokenengine-model", j.get("model") or "")
        self.end_headers()
        for et, obj in events:
            self.wfile.write(self._sse(et, obj))
            self.wfile.flush()
        self.close_connection = True

    def _resp_input_to_chat(self, inp):
        """Responses input[] -> OpenAI chat messages[]。"""
        if isinstance(inp, str):
            return [{"role": "user", "content": inp}]
        if not isinstance(inp, list):
            return []
        out = []
        for it in inp:
            t = it.get("type")
            if t == "message":
                role = it.get("role", "user")
                if role == "developer":
                    role = "system"
                text_parts = []
                image_parts = []
                for c in it.get("content", []):
                    if isinstance(c, str):
                        text_parts.append(c)
                        continue
                    cty = c.get("type")
                    if cty in ("text", "input_text", "output_text"):
                        text_parts.append(c.get("text", ""))
                    else:
                        url = extract_image_url(c)
                        if url:
                            image_parts.append({"type": "image_url", "image_url": {"url": url}})
                text = "".join(text_parts)
                if image_parts:
                    content = ([{"type": "text", "text": text}] if text else []) + image_parts
                    out.append({"role": role, "content": content})
                else:
                    out.append({"role": role, "content": text})
            elif t == "function_call":
                cid = it.get("call_id") or it.get("id")
                out.append({"role": "assistant", "tool_calls": [{"id": cid, "type": "function",
                    "function": {"name": it.get("name", ""), "arguments": it.get("arguments", "")}}]})
            elif t == "function_call_output":
                cid = it.get("call_id") or it.get("id")
                content = it.get("output", "")
                if not isinstance(content, str):
                    content = json.dumps(content, ensure_ascii=False)
                out.append({"role": "tool", "tool_call_id": cid, "content": content})
            elif t in ("computer_call_output", "custom_tool_call_output"):
                # Codex computer-use 截图回传：OpenAI 专有 item，转成带图的 user 消息，避免截图被丢弃
                o = it.get("output")
                img = None
                if isinstance(o, dict):
                    img = o.get("image_url") or o.get("image")
                    if not img and o.get("data"):
                        d = o["data"]
                        if isinstance(d, str):
                            mime = o.get("mime_type") or "image/png"
                            img = "data:" + mime + ";base64," + d
                elif isinstance(o, str):
                    img = o
                if img:
                    out.append({"role": "user", "content": [
                        {"type": "text", "text": "[当前电脑屏幕截图] 请先看清屏幕上的内容，再据此回答或给出下一步操作："},
                        {"type": "image_url", "image_url": {"url": img}}]})
        return out

    def _resp_tools_to_chat(self, tools):
        """Responses tools（function/namespace）-> OpenAI chat tools；忽略 strict 与 web_search。"""
        out = []
        if not isinstance(tools, list):
            return out

        def add(fn_def):
            out.append({"type": "function", "function": {
                "name": fn_def.get("name"), "description": fn_def.get("description", ""),
                "parameters": fn_def.get("parameters", {"type": "object", "properties": {}})}})

        for t in tools:
            ty = t.get("type")
            if ty == "function":
                add(t)
            elif ty == "namespace" and isinstance(t.get("tools"), list):
                for sub in t["tools"]:
                    if sub.get("type") == "function":
                        add(sub)
        return out

class _Server(ThreadingHTTPServer):
    daemon_threads = True      # 工作线程设为守护线程，卡住的上游请求不会阻止进程退出/重启
    allow_reuse_address = True

def main():
    def _thread_excepthook(args):
        try:
            log_line("thread error: " + "".join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback)))
        except Exception:
            pass
    threading.excepthook = _thread_excepthook
    _init_live()
    cfg = load_cfg()
    addr = (cfg.get("listen_host", "0.0.0.0"), int(cfg.get("listen_port", 8317)))
    srv = _Server(addr, Handler)
    log_line(f"TokenEngine router on http://{addr[0]}:{addr[1]}  (Tailscale: http://<this-ip>:{addr[1]}/v1)")
    log_line(f"providers: {[p['name'] for p in cfg['providers']]}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception:
        log_line("server fatal: " + traceback.format_exc())
        raise

if __name__ == "__main__":
    main()
