import base64, json, urllib.request

IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
durl = "data:image/png;base64," + base64.b64encode(open(IMG, "rb").read()).decode()
BASE = "http://127.0.0.1:8317"

def post(path, body):
    r = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST")
    r.add_header("Content-Type", "application/json")
    with urllib.request.build_opener().open(r, timeout=90) as resp:
        return resp.read().decode("utf-8", "ignore")

# A: /v1/chat/completions, model=vision, stream=true (WorkBuddy 路径)
bodyA = {"model": "vision", "stream": True, "messages": [{"role": "user", "content": [
    {"type": "text", "text": "左右分别是什么软件?一句话。"},
    {"type": "image_url", "image_url": {"url": durl}}]}]}
txtA = ""
for line in post("/v1/chat/completions", bodyA).splitlines():
    if line.startswith("data:") and "[DONE]" not in line:
        try:
            d = json.loads(line[5:].strip())
            txtA += d["choices"][0]["delta"].get("content", "") or ""
        except Exception:
            pass
print("A chat/vision ->", txtA)

# B: /v1/responses, input_image (Codex 路径，model 填 auto 让 router 自动识别图片)
bodyB = {"model": "auto", "max_output_tokens": 200,
         "input": [{"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "左右分别是什么软件?一句话。"},
             {"type": "input_image", "image_url": durl}]}]}
txtB = ""
for raw in post("/v1/responses", bodyB).split("\n"):
    if raw.startswith("data:"):
        try:
            d = json.loads(raw[5:].strip())
            if d.get("type") == "response.output_text.delta":
                txtB += d.get("delta", "")
        except Exception:
            pass
print("B responses/vision ->", txtB)
