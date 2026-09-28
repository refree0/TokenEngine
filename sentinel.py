#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenEngine Sentinel —— 专职"发电工"。
定期探测每个免费源的健康度，写 logs/token_pool.json。router 据此自动跳过坏源。
用法：python sentinel.py            # 探测一次后退出（适合被任务计划周期调用）
      python sentinel.py --loop 300 # 常驻，每 300 秒探测一次
"""
import json, os, sys, time, urllib.request, urllib.error, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config", "providers.json")
POOL = os.path.join(HERE, "logs", "token_pool.json")

def load_cfg():
    with open(CFG, "r", encoding="utf-8") as f:
        return json.load(f)

def probe(p):
    """发一个最小 chat 请求判断该源是否活着、key 是否有效。"""
    if not p.get("enabled", True):
        return {"ok": False, "detail": "disabled"}
    model = p.get("model_map", {}).get("auto", p.get("models_out", ["gpt-3.5-turbo"])[0])
    url = p["base_url"].rstrip("/") + "/chat/completions"
    payload = {"model": model, "messages": [{"role": "user", "content": "ping"}],
               "max_tokens": 1, "stream": False}
    data = json.dumps(payload).encode("utf-8")
    r = urllib.request.Request(url, data=data, method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", "Bearer " + p["api_key"])
    px = p.get("proxy")
    op = urllib.request.build_opener(urllib.request.ProxyHandler({"http": px, "https": px})) if px else urllib.request.build_opener()
    t0 = time.time()
    try:
        with op.open(r, timeout=30) as resp:
            return {"ok": True, "detail": f"HTTP {resp.status}", "latency_ms": int((time.time()-t0)*1000)}
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read()[:200].decode("utf-8", "ignore")
        except Exception: pass
        # 401/402/403 = key 失效或欠费；402 也可能是没余额
        return {"ok": e.code not in (401, 402, 403), "detail": f"HTTP {e.code} {body}",
                "latency_ms": int((time.time()-t0)*1000)}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__} {e}",
                "latency_ms": int((time.time()-t0)*1000)}

def run_once():
    cfg = load_cfg()
    result = {"checked": datetime.datetime.now().isoformat(timespec="seconds"), "providers": {}}
    alive = 0
    for p in cfg["providers"]:
        st = probe(p)
        result["providers"][p["name"]] = {**st, "priority": p.get("priority")}
        if st.get("ok"): alive += 1
        print(f"[probe] {p['name']:14s} ok={st.get('ok')} {st.get('detail','')[:80]}", flush=True)
    result["alive_count"] = alive
    result["total"] = len(cfg["providers"])
    if alive == 0:
        result["ACTION"] = "ALL_PROVIDERS_DOWN — 需人工介入/补新源"
    elif alive <= 1:
        result["ACTION"] = "WARNING — 仅剩一个源，接近耗尽阈值，准备切换备用源"
    else:
        result["ACTION"] = "OK"
    os.makedirs(os.path.dirname(POOL), exist_ok=True)
    tmp = POOL + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    os.replace(tmp, POOL)
    print(f"[probe] alive={alive}/{result['total']} action={result['ACTION']}", flush=True)
    return result

def main():
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        interval = int(sys.argv[i+1]) if i+1 < len(sys.argv) else 300
        while True:
            run_once()
            time.sleep(interval)
    else:
        run_once()

if __name__ == "__main__":
    main()
