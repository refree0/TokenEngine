# -*- coding: utf-8 -*-
import base64, json, urllib.request, urllib.error

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
with open(IMG, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()

payload = {"model": "gpt-5", "stream": False, "input": [
    {"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": "弹窗里的模型名是什么？只回答模型名。"},
        {"type": "input_image", "image_url": "data:image/png;base64," + b64}]}]}
body = json.dumps(payload).encode("utf-8")
r = urllib.request.Request("http://127.0.0.1:8317/v1/responses", data=body,
                           headers={"Content-Type": "application/json"})
try:
    with op.open(r, timeout=90) as resp:
        print("provider =", resp.headers.get("x-tokenengine-provider"))
        text = ""
        for line in resp.read().decode("utf-8").splitlines():
            if line.startswith("data:"):
                d = line[5:].strip()
                if d and d != "[DONE]":
                    try:
                        o = json.loads(d)
                        if o.get("type") == "response.output_text.delta":
                            text += o.get("delta", "")
                    except Exception:
                        pass
        print("TEXT:", text)
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode("utf-8", "ignore")[:400])
