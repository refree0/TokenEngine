#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TokenEngine 全链路回归测试。

覆盖所有对外端点，改完 router.py / providers.json 后跑一遍，确认没有回归。

用法::

    python test_regression.py

退出码 0 = 全部通过，1 = 有失败项。
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("TOKENENGINE_BASE", "http://127.0.0.1:8317")
HERE = os.path.dirname(os.path.abspath(__file__))
CARD = os.path.join(HERE, "logs", "vision_test_card.png")

results = []


def post(path, body, headers=None, timeout=60):
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)
    except Exception as e:
        return "ERR", str(e), {}


def check(name, cond, detail=""):
    results.append((name, bool(cond), detail))
    print(("✅" if cond else "❌") + " " + name + "  " + detail)


def main():
    print("=" * 68)
    print("TokenEngine 全链路回归测试  ->  " + BASE)
    print("=" * 68)

    # 1. GET /v1/models
    try:
        with urllib.request.urlopen(BASE + "/v1/models", timeout=10) as r:
            n = len(json.loads(r.read()).get("data", []))
        check("1. GET /v1/models", r.status == 200 and n > 0, f"{n} 个模型")
    except Exception as e:
        check("1. GET /v1/models", False, str(e)[:60])

    # 2. Chat 非流式
    s, b, h = post("/v1/chat/completions",
                   {"model": "auto", "messages": [{"role": "user", "content": "reply: OK1"}],
                    "max_tokens": 10})
    check("2. Chat 非流式", s == 200 and "choices" in b,
          f"HTTP {s} provider={h.get('x-tokenengine-provider','')}")

    # 3. Chat 流式
    s, b, h = post("/v1/chat/completions",
                   {"model": "auto", "messages": [{"role": "user", "content": "reply: OK2"}],
                    "max_tokens": 10, "stream": True})
    check("3. Chat 流式 SSE", s == 200 and "data:" in b and "[DONE]" in b,
          f"HTTP {s} 含[DONE]={'[DONE]' in b}")

    # 4. Anthropic 非流式（Claude Code）
    s, b, h = post("/v1/messages",
                   {"model": "claude-sonnet-4-5", "max_tokens": 15,
                    "messages": [{"role": "user", "content": "reply: OK3"}]},
                   {"anthropic-version": "2023-06-01", "Authorization": "Bearer x"})
    check("4. Anthropic 非流式", s == 200 and '"type": "message"' in b and '"id": "msg_' in b,
          f"HTTP {s} msg_前缀={'\"id\": \"msg_' in b}")

    # 5. Anthropic 流式
    s, b, h = post("/v1/messages",
                   {"model": "claude-sonnet-4-5", "max_tokens": 15, "stream": True,
                    "messages": [{"role": "user", "content": "reply: OK4"}]},
                   {"anthropic-version": "2023-06-01", "Authorization": "Bearer x"})
    evs = [l for l in b.split("\n") if l.startswith("event:")]
    check("5. Anthropic 流式 SSE", s == 200 and len(evs) >= 6,
          f"HTTP {s} {len(evs)} 个事件")

    # 6. Responses（Codex）
    s, b, h = post("/v1/responses",
                   {"model": "auto", "input": "reply: OK5", "stream": True,
                    "max_output_tokens": 20})
    check("6. Responses (Codex)", s == 200 and ("response." in b or "data:" in b),
          f"HTTP {s}")

    # 7. Vision 识图
    if os.path.exists(CARD):
        b64 = base64.b64encode(open(CARD, "rb").read()).decode()
        s, b, h = post("/v1/chat/completions",
                       {"model": "vision", "max_tokens": 100,
                        "messages": [{"role": "user", "content": [
                            {"type": "text", "text": "图里有什么字"},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/png;base64,{b64}"}}]}]})
        check("7. Vision 识图", s == 200,
              f"HTTP {s} provider={h.get('x-tokenengine-provider','')} "
              f"model={h.get('x-tokenengine-model','')}")
    else:
        check("7. Vision 识图", True, f"跳过（找不到 {CARD}）")

    # 8. 显式路由 provider:model
    s, b, h = post("/v1/chat/completions",
                   {"model": "bailian:auto",
                    "messages": [{"role": "user", "content": "reply: OK6"}], "max_tokens": 10})
    check("8. 显式路由 bailian:auto", s == 200 and h.get("x-tokenengine-provider") == "bailian",
          f"HTTP {s} provider={h.get('x-tokenengine-provider')} "
          f"model={h.get('x-tokenengine-model')}")

    # 9. 思考模式：流式 delta 必须透传 reasoning_content
    #    （否则客户端下一轮无法回传，上游报 400 "reasoning content must be passed back"）
    #    ⚠️ 必须钉一个「确定会吐思维链」的源。用 auto 时结果取决于本轮轮到哪个上游，
    #    例如 sensenova 的 deepseek-v4-flash 不返回 reasoning_content，断言会随机失败 ——
    #    那是上游差异，不是 router 的问题。
    _keys, _used = set(), ""
    for _m in ("modelscope:Qwen/Qwen3.5-35B-A3B",
               "modelscope:deepseek-ai/DeepSeek-V4-Flash-0731",
               "auto"):
        s, b, h = post("/v1/chat/completions",
                       {"model": _m, "stream": True, "max_tokens": 800,
                        "messages": [{"role": "user", "content": "算 123*456，给出过程"}]})
        _d = set()
        for _line in b.split("\n"):
            if _line.startswith("data: ") and "[DONE]" not in _line:
                try:
                    _dd = json.loads(_line[6:])["choices"][0].get("delta", {})
                    if _dd:
                        _d.update(_dd.keys())
                except Exception:
                    pass
        if _d:
            _keys, _used = _d, _m
        if "reasoning_content" in _d:
            break
    check("9. 思考模式 reasoning 透传", "reasoning_content" in _keys,
          f"model={_used} delta 字段={sorted(_keys)}")

    # 10. /health 含账号池
    try:
        with urllib.request.urlopen(BASE + "/health", timeout=10) as r:
            j = json.loads(r.read())
        acc = j.get("accounts", {})
        check("10. /health 账号池", j.get("ok") and len(acc) > 0,
              f"{len(acc)} 个 provider 池")
    except Exception as e:
        check("10. /health 账号池", False, str(e)[:60])

    print("=" * 68)
    passed = sum(1 for _, c, _ in results if c)
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
