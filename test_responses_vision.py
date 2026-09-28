import base64, json, urllib.request, urllib.error

IMG = __import__("os").environ.get("TOKENENGINE_TEST_IMAGE", "test_image.png")
b64 = base64.b64encode(open(IMG,"rb").read()).decode()

body = {
  "model": "gpt-5",
  "input": [
    {"type": "message", "role": "user", "content": [
      {"type": "input_text", "text": "这是什么网页?一句话。"},
      {"type": "input_image", "image_url": {"url": "data:image/png;base64,"+b64}}
    ]}
  ],
  "stream": True
}
r = urllib.request.Request("http://127.0.0.1:8317/v1/responses",
    data=json.dumps(body).encode(), method="POST")
r.add_header("Content-Type","application/json")
r.add_header("Accept","text/event-stream")
try:
    with urllib.request.urlopen(r, timeout=120) as resp:
        print("provider:", resp.headers.get("x-tokenengine-provider"))
        txt = resp.read().decode("utf-8","ignore")
        for line in txt.splitlines():
            if "output_text.delta" in line or line.startswith("event: response.completed"):
                print(line[:400])
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read()[:800].decode("utf-8","ignore"))
except Exception as e:
    print("ERR", type(e).__name__, e)
