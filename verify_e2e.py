# -*- coding: utf-8 -*-
import base64, json, urllib.request, urllib.error

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
with open(IMG, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

def chat(model, messages):
    body = json.dumps({"model": model, "messages": messages, "max_tokens": 200}).encode("utf-8")
    r = urllib.request.Request("http://127.0.0.1:8317/v1/chat/completions", data=body,
                               headers={"Content-Type": "application/json"})
    try:
        with op.open(r, timeout=90) as resp:
            j = json.loads(resp.read().decode("utf-8"))
            return "provider=" + str(resp.headers.get("x-tokenengine-provider")), j["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return "HTTP %d" % e.code, e.read().decode("utf-8", "ignore")[:300]

print("### 1) TEXT auto")
p, out = chat("auto", [{"role": "user", "content": "用一句话说你好"}])
print(p); print(out)
print("")
print("### 2) VISION vision (云端视觉)")
p, out = chat("vision", [{"role": "user", "content": [
    {"type": "text", "text": "弹窗里的模型名是什么？只回答模型名。"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}])
print(p); print(out)
