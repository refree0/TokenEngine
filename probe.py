import urllib.request, json
key=__import__("os").environ.get("AIHUBMIX_API_KEY","")
body=json.dumps({"model":"coding-glm-5.3-free","messages":[{"role":"user","content":"ping"}],"max_tokens":5}).encode()
for proxy in [None, "http://127.0.0.1:10808"]:
    op=urllib.request.build_opener() if proxy is None else urllib.request.build_opener(urllib.request.ProxyHandler({"http":proxy,"https":proxy}))
    req=urllib.request.Request("https://aihubmix.com/v1/chat/computations", data=body, method="POST")
    req=urllib.request.Request("https://aihubmix.com/v1/chat/completions", data=body, method="POST")
    req.add_header("Authorization","Bearer "+key); req.add_header("Content-Type","application/json")
    try:
        with op.open(req, timeout=20) as r:
            print(proxy, "OK", r.status)
    except Exception as e:
        print(proxy, "FAIL", type(e).__name__, str(e)[:80])
