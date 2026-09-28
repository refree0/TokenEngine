# -*- coding: utf-8 -*-
import json, urllib.request, urllib.error

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(model):
    payload = {"model": model, "stream": False,
               "messages": [{"role": "user", "content": "用一句话回答：1+1等于几？"}]}
    body = json.dumps(payload).encode("utf-8")
    r = urllib.request.Request("http://127.0.0.1:8317/v1/chat/completions", data=body,
                               headers={"Content-Type": "application/json"})
    try:
        with op.open(r, timeout=60) as resp:
            j = json.loads(resp.read().decode("utf-8"))
            txt = j["choices"][0]["message"].get("content", "")
            print("model=%-38s -> provider=%s real=%s" % (
                model, resp.headers.get("x-tokenengine-provider"),
                resp.headers.get("x-tokenengine-model")))
            print("    text:", txt[:80])
    except urllib.error.HTTPError as e:
        print("model=%s -> HTTP %s %s" % (model, e.code,
              e.read().decode("utf-8", "ignore")[:150]))


for m in ["amd:MiMo-V2.6-Flash", "DeepSeek-V4-Flash-Vision-Exp",
          "kimi-k3", "Qwen/Qwen3.5-27B", "auto"]:
    call(m)
