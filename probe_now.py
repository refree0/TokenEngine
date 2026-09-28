# -*- coding: utf-8 -*-
"""快速探测各免费源当前真实状态：短请求 + 长上下文请求（>16k tokens）"""
import json, os, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "config", "providers.json"), encoding="utf-8") as f:
    cfg = json.load(f)

def opener_for(p):
    px = p.get("proxy")
    if px:
        return urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px}))
    return urllib.request.build_opener()

def call(p, model, messages, timeout=45):
    url = p["base_url"].rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": messages, "max_tokens": 16, "stream": False}
    data = json.dumps(payload).encode("utf-8")
    rq = urllib.request.Request(url, data=data, method="POST")
    rq.add_header("Content-Type", "application/json")
    rq.add_header("Authorization", "Bearer " + p["api_key"])
    t0 = time.time()
    try:
        with opener_for(p).open(rq, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "ignore")
            dt = time.time() - t0
            j = json.loads(body)
            txt = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")[:30]
            return f"OK {dt:5.1f}s  {txt!r}", True
    except urllib.error.HTTPError as e:
        dt = time.time() - t0
        body = e.read()[:180].decode("utf-8", "ignore")
        return f"HTTP {e.code} {dt:5.1f}s  {body}", False
    except Exception as e:
        dt = time.time() - t0
        return f"{type(e).__name__} {dt:5.1f}s  {e}", False

print("=" * 70)
print("短请求探测（auto 模型，模拟普通 Codex 问答）")
print("=" * 70)
for p in sorted(cfg["providers"], key=lambda x: x.get("priority", 99)):
    if not p.get("enabled", True):
        print(f"[p{p['priority']}] {p['name']:12s} DISABLED")
        continue
    mm = p.get("model_map", {})
    model = mm.get("auto")
    if isinstance(model, list):
        model = model[0]
    print(f"[p{p['priority']}] {p['name']:12s} ({model}) ...", flush=True)
    msg, ok = call(p, model, [{"role": "user", "content": "只回复两个字：在的"}])
    print(f"      -> {msg}")

print()
print("=" * 70)
print("长上下文探测（约 20k tokens，模拟 Codex 长会话，验证 16k 上限问题）")
print("=" * 70)
# 约 20k tokens：中文按 1 字 ~1 token 粗估，英文 ~4 字符/token；用重复英文段落约 80k 字符
filler = ("The quick brown fox jumps over the lazy dog. " * 1600)  # ~72k chars ~18k tokens
long_msgs = [{"role": "user", "content": filler + "\n\n只回复两个字：收到"}]
for p in sorted(cfg["providers"], key=lambda x: x.get("priority", 99)):
    if not p.get("enabled", True):
        continue
    mm = p.get("model_map", {})
    model = mm.get("auto")
    if isinstance(model, list):
        model = model[0]
    print(f"[p{p['priority']}] {p['name']:12s} ({model}) ...", flush=True)
    msg, ok = call(p, model, long_msgs, timeout=60)
    print(f"      -> {msg}")
