# -*- coding: utf-8 -*-
import base64, json, time, urllib.request, urllib.error

KEY = __import__("os").environ.get("BAILIAN_API_KEY","")
BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(model, messages, timeout=90):
    body = json.dumps({"model": model, "messages": messages, "max_tokens": 128}).encode("utf-8")
    req = urllib.request.Request(BASE + "/chat/completions", data=body,
          headers={"Content-Type": "application/json", "Authorization": "Bearer " + KEY})
    t0 = time.time()
    try:
        with opener.open(req, timeout=timeout) as r:
            j = json.loads(r.read().decode("utf-8"))
            return r.status, time.time() - t0, j["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        return e.code, time.time() - t0, e.read().decode("utf-8", "ignore")[:400]
    except Exception as e:
        return -1, time.time() - t0, type(e).__name__ + " " + str(e)

print("### TEXT qwen3.5-flash")
st, dt, out = call("qwen3.5-flash", [{"role": "user", "content": "说你好，一句话"}])
print(st, "%.2fs" % dt, str(out)[:200])
print("")

with open(IMG, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
print("### VISION qwen3-vl-flash")
st, dt, out = call("qwen3-vl-flash", [{"role": "user", "content": [
    {"type": "text", "text": "弹窗里的模型名是什么？一句话回答。"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}])
print(st, "%.2fs" % dt, str(out)[:300])
