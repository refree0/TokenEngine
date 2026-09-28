import base64, json, urllib.request, urllib.error

IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
raw = open(IMG, "rb").read()
print("img bytes:", len(raw))
b64 = base64.b64encode(raw).decode()
data_url = "data:image/png;base64," + b64

cands = [
  ("bigmodel-glm4v", "https://open.bigmodel.cn/api/paas/v4/chat/completions",
   __import__("os").environ.get("ZHIPU_API_KEY",""), "glm-4v-flash", None),
  ("siliconflow-qwenvl", "https://api.siliconflow.cn/v1/chat/completions",
   __import__("os").environ.get("SILICONFLOW_API_KEY",""), "Qwen/Qwen2.5-VL-7B-Instruct", None),
]
for name, url, key, model, proxy in cands:
    body = {"model": model, "messages": [{"role": "user", "content": [
        {"type": "text", "text": "这张图里左右两个窗口分别是什么软件?用一句话回答。"},
        {"type": "image_url", "image_url": {"url": data_url}}]}], "max_tokens": 200}
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    op = urllib.request.build_opener(*handlers)
    r = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    r.add_header("Content-Type", "application/json")
    r.add_header("Authorization", "Bearer " + key)
    try:
        with op.open(r, timeout=60) as resp:
            j = json.loads(resp.read().decode())
            print(f"[{name}] {model} OK ->", j["choices"][0]["message"]["get" if False else "content"][:200])
    except urllib.error.HTTPError as e:
        print(f"[{name}] {model} HTTP {e.code}:", e.read()[:300].decode("utf-8", "ignore"))
    except Exception as e:
        print(f"[{name}] {model} ERR", type(e).__name__, e)
