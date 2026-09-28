# -*- coding: utf-8 -*-
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import base64, json, os, shutil, urllib.request, urllib.error

CFG = os.path.join(_ROOT, "config", "providers.json")
BAK = os.path.join(_ROOT, "config", "providers.bak_ocrtest.json")
IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
shutil.copy(CFG, BAK)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
    for p in cfg["providers"]:
        p["enabled"] = False
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    with open(IMG, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    body = json.dumps({"model": "vision", "messages": [{"role": "user", "content": [
        {"type": "text", "text": "图里有什么文字？"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}],
        "max_tokens": 300}).encode("utf-8")
    r = urllib.request.Request("http://127.0.0.1:8317/v1/chat/completions", data=body,
                               headers={"Content-Type": "application/json"})
    try:
        with op.open(r, timeout=90) as resp:
            j = json.loads(resp.read().decode("utf-8"))
            print("provider =", resp.headers.get("x-tokenengine-provider"))
            print(j["choices"][0]["message"]["content"][:400])
    except urllib.error.HTTPError as e:
        print("HTTP", e.code, e.read().decode("utf-8", "ignore")[:300])
finally:
    shutil.copy(BAK, CFG)
    os.remove(BAK)
    print("--- providers.json restored ---")
