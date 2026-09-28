# -*- coding: utf-8 -*-
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import base64, json, time, urllib.request, urllib.error

ROUTER = "http://127.0.0.1:8317/v1/chat/completions"
CFG = os.path.join(_ROOT, "config", "providers.json")
IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def chat(model, messages, max_tokens=128):
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": max_tokens, "temperature": 0.2}).encode("utf-8")
    req = urllib.request.Request(ROUTER, data=body,
          headers={"Content-Type": "application/json", "Authorization": "Bearer any"})
    t0 = time.time()
    try:
        with opener.open(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
            return r.status, time.time() - t0, r.headers.get("x-tokenengine-provider"), data["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.headers.get("x-tokenengine-provider"), e.read().decode("utf-8", "ignore")[:400]

with open(IMG, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
vmsg = [{"role": "user", "content": [
    {"type": "text", "text": "弹窗标题里的模型名是什么？一句话回答。"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]

print("### TEST1: explicit AMD vision model via router")
st, dt, prov, content = chat("DeepSeek-V4-Flash-Vision-Exp", vmsg)
print("HTTP %s  latency %.2fs  provider=%s" % (st, dt, prov))
print(str(content)[:300])
print("")

print("### TEST2: failover - disable sensenova/bigmodel, auto must land on amd")
with open(CFG, "r", encoding="utf-8") as f:
    cfg = json.load(f)
orig = {}
try:
    for p in cfg["providers"]:
        if p["name"] in ("sensenova", "bigmodel"):
            orig[p["name"]] = p["enabled"]
            p["enabled"] = False
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    time.sleep(1.5)
    st, dt, prov, content = chat("auto", [{"role": "user", "content": "用一句话打招呼，并给出你的模型名。"}])
    print("HTTP %s  latency %.2fs  provider=%s" % (st, dt, prov))
    print(str(content)[:300])
finally:
    with open(CFG, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for p in cfg["providers"]:
        if p["name"] in orig:
            p["enabled"] = orig[p["name"]]
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("restored: " + json.dumps({p["name"]: p["enabled"] for p in cfg["providers"] if p["name"] in orig}))
