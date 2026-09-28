# -*- coding: utf-8 -*-
import base64, json, time, urllib.request, urllib.error

KEY = __import__("os").environ.get("AMD_API_KEY","")
BASE = "https://developer.amd.com.cn/radeon/api/v1"
IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def call(tag, model, messages, max_tokens=200):
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": max_tokens, "temperature": 0.2}).encode("utf-8")
    req = urllib.request.Request(BASE + "/chat/completions", data=body, headers={
        "Authorization": "Bearer " + KEY, "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with opener.open(req, timeout=90) as r:
            data = json.loads(r.read().decode("utf-8"))
            print("=== %s ===" % tag)
            print("HTTP %s  latency %.2fs" % (r.status, time.time() - t0))
            print(data["choices"][0]["message"]["content"].strip())
    except urllib.error.HTTPError as e:
        print("=== %s ===" % tag)
        print("HTTP %s  latency %.2fs" % (e.code, time.time() - t0))
        print(e.read().decode("utf-8", "ignore")[:600])
    except Exception as e:
        print("=== %s ===" % tag)
        print("ERROR latency %.2fs: %s" % (time.time() - t0, e))
    print("")

with open(IMG, "rb") as f:
    b64 = base64.b64encode(f.read()).decode()
img_msg = [{"role": "user", "content": [
    {"type": "text", "text": "这是什么软件/网站的界面？弹窗标题里的模型名和 Base URL 分别是什么？简短回答。"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64," + b64}}]}]

call("TEXT  DeepSeek-V4-Flash (1M)", "DeepSeek-V4-Flash",
     [{"role": "user", "content": "用一句话介绍你自己，并给出你的模型名。"}], 128)
call("VISION DeepSeek-V4-Flash-Vision-Exp", "DeepSeek-V4-Flash-Vision-Exp", img_msg, 256)
call("VISION Qwen3.8-27B", "Qwen3.8-27B", img_msg, 256)
