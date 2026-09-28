import base64, json, urllib.request, urllib.error, sys

IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
raw = open(IMG, "rb").read()
print("img bytes:", len(raw))
b64 = base64.b64encode(raw).decode()
data_url = "data:image/png;base64," + b64

body = {"model": "vision", "messages": [{"role": "user", "content": [
    {"type": "text", "text": "这是什么网页?用一句话回答。"},
    {"type": "image_url", "image_url": {"url": data_url}}]}], "max_tokens": 200}
r = urllib.request.Request("http://127.0.0.1:8317/v1/chat/completions",
                            data=json.dumps(body).encode(), method="POST")
r.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(r, timeout=90) as resp:
        print("provider header:", resp.headers.get("x-tokenengine-provider"))
        j = json.loads(resp.read().decode())
        print("VISION OK ->", j["choices"][0]["message"]["content"][:300])
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:500].decode("utf-8","ignore"))
except Exception as e:
    print("ERR", type(e).__name__, e)
