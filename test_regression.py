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
    #    ⚠️ 不要钉死某一个源：免费源会因额度耗尽 / 模型被暂停而临时不可用
    #    （bailian 免费额度耗尽、volcengine 模型暂停都会让固定断言随机失败）。
    #    按可用性顺序依次试，命中「200 且 x-tokenengine-provider 等于指定源」即算通过。
    _got, _p8, _last8 = None, "", ""
    for _m8 in ("amd:auto", "antigravity:auto", "sensenova:auto", "modelscope:auto"):
        _p8 = _m8.split(":")[0]
        s, b, h = post("/v1/chat/completions",
                       {"model": _m8, "messages": [{"role": "user", "content": "reply: OK6"}],
                        "max_tokens": 10})
        _got = h.get("x-tokenengine-provider")
        _last8 = f"{_m8} -> HTTP {s} provider={_got} model={h.get('x-tokenengine-model')}"
        if s == 200 and _got == _p8:
            break
    check("8. 显式路由 provider:model", s == 200 and _got == _p8, _last8)

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

    # 11. 超长请求：所有源都装不下时返回明确错误，且不再挨个砸小窗口源
    #     （打卡·下一步行动 第 1 条。旧逻辑 `if fitted:` 失效后原样放行，
    #      把超长请求挨个砸进 16k 窗口的 bigmodel，白撞 400 + 白等 N 轮往返，
    #      压力测试里 55 次超窗 400 都这么来的。现在只挑窗口最大的那一个试一次。）
    #     走单元级验证而非真请求：amd 实测能吞下 43 万 token，真请求触发不了
    #     「所有源都装不下」这条分支，而且 3.5MB 上传要跑两分钟。
    try:
        import router as _r
        _huge = "a" * 3_500_000          # est ≈110 万 token > 所有 context_window（最大 1M）
        _msgs = [{"role": "user", "content": _huge}]
        _tried = []
        _orig = (_r._try_provider, _r.log_line)
        _r._try_provider = lambda p, *a, **k: (_tried.append(p["name"]), (None, None, "boom"))[1]
        _r.log_line = lambda *a, **k: None
        try:
            _j, _p, _e = _r.chat_call(_msgs, model="auto", max_tokens=1)
        finally:
            (_r._try_provider, _r.log_line) = _orig
        check("11. 超长请求明确报错",
              _j is None and "request too large" in (_e or "") and len(_tried) == 1,
              f"只试了 {_tried}（旧逻辑会试全部），错误含request_too_large="
              f"{'request too large' in (_e or '')}")
    except Exception as e:
        check("11. 超长请求明确报错", False, f"{type(e).__name__}: {str(e)[:70]}")

    # 12. 截断空内容自动重试（打卡·下一步行动 第 1 条）
    #     单元级验证「finish_reason=length + 空内容 → 同源放宽 max_tokens 重试一次」的接线：
    #     伪造 opener 让第一次返回空内容，断言 _retry_truncated 被调用且结果被采纳。
    try:
        import router as _r

        class _FakeResp:
            def __init__(self, obj):
                self._b = json.dumps(obj).encode()

            def read(self):
                return self._b

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class _FakeOpener:
            def open(self, rq, timeout=None):
                return _FakeResp({"choices": [{"index": 0, "finish_reason": "length",
                                               "message": {"role": "assistant", "content": ""}}],
                                  "model": "m1", "usage": {}})

        _seen = {}
        _orig = (_r.opener_for, _r._retry_truncated, _r.mark_fail, _r.set_cooldown,
                 _r.state_save, _r.log_line)
        _r.opener_for = lambda p: _FakeOpener()

        def _fake_retry(p, real_model, use_key, messages, tools, max_tokens, extra, timeout, want):
            _seen["called"] = (real_model, max_tokens)
            return {"choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": "正文"}}],
                    "model": real_model, "usage": {}}

        _r._retry_truncated = _fake_retry
        _r.mark_fail = lambda *a, **k: None          # 别污染 fail.log / LIVE
        _r.set_cooldown = lambda *a, **k: None
        _r.state_save = lambda *a, **k: None
        _r.log_line = lambda *a, **k: None
        try:
            _j, _m, _e = _r._try_provider(
                {"name": "fakeprov", "base_url": "http://127.0.0.1:1/v1", "api_key": "k",
                 "model_map": {"auto": ["m1"]}, "timeout": 5},
                "auto", [{"role": "user", "content": "hi"}], None, 60)
        finally:
            (_r.opener_for, _r._retry_truncated, _r.mark_fail, _r.set_cooldown,
             _r.state_save, _r.log_line) = _orig
        check("12. 截断空内容自动重试",
              _j is not None and _seen.get("called") == ("m1", 60) and _m == "m1",
              f"重试被调用={_seen.get('called')} 采纳结果={_j is not None} model={_m}")
    except Exception as e:
        check("12. 截断空内容自动重试", False, f"{type(e).__name__}: {str(e)[:70]}")

    # 13. tool_calls 非流式（报告·下一步建议 第 1 条）
    #     补 function calling 之前，shim 只做纯对话，Claude 无法被当 Agent 驱动，
    #     也就接不进 Claude Code CLI / Cline / Continue 这些真正吃 tool_calls 的客户端。
    #     覆盖点：tools[].function.parameters 映射成 functionDeclarations、
    #     parts[].functionCall 反向转成 finish_reason=tool_calls + tool_calls[]。
    _TOOLS = [{"type": "function", "function": {
        "name": "get_weather", "description": "查询指定城市的当前天气",
        "parameters": {"type": "object", "$schema": "http://json-schema.org/draft-07/schema#",
                       "additionalProperties": False,
                       "properties": {"city": {"type": "string", "description": "城市名"}},
                       "required": ["city"]}}}]
    _tc_model, _tc1, _tc_err = None, None, ""
    for _m13 in ("antigravity:claude-sonnet-4-6", "antigravity:auto", "amd:auto"):
        s, b, h = post("/v1/chat/completions",
                       {"model": _m13, "max_tokens": 300, "tools": _TOOLS,
                        "messages": [{"role": "user", "content": "北京天气如何？用工具查。"}]})
        if s == 200:
            try:
                _c = json.loads(b)["choices"][0]
                if (_c.get("message") or {}).get("tool_calls"):
                    _tc_model, _tc1 = _m13, _c
                    break
            except Exception as e:
                _tc_err = str(e)[:60]
        else:
            _tc_err = f"HTTP {s} {b[:80]}"
    _tc_name = ((((_tc1 or {}).get("message") or {}).get("tool_calls") or [{}])[0]
                .get("function") or {}).get("name")
    check("13. tool_calls 非流式",
          _tc1 is not None and _tc1.get("finish_reason") == "tool_calls" and _tc_name == "get_weather",
          f"model={_tc_model} finish={(_tc1 or {}).get('finish_reason')} tool={_tc_name} {_tc_err}")

    # 14. tool_calls 多轮闭环：tool_call -> 回传工具结果 -> 最终回答
    #     这一条才是 Agent 真正跑得起来的分水岭：缺 id 会让上游报
    #     `messages.N.content.M.tool_use.id: Field required`（实测踩过）。
    _tc2, _tc2_err = None, ""
    if _tc1 is not None:
        _call = ((_tc1.get("message") or {}).get("tool_calls") or [{}])[0]
        s, b, h = post("/v1/chat/completions",
                       {"model": _tc_model, "max_tokens": 400, "tools": _TOOLS,
                        "messages": [
                            {"role": "user", "content": "北京天气如何？用工具查。"},
                            {"role": "assistant", "content": (_tc1.get("message") or {}).get("content"),
                             "tool_calls": [_call]},
                            {"role": "tool", "tool_call_id": _call.get("id"), "name": "get_weather",
                             "content": json.dumps({"city": "北京", "temp_c": 21, "weather": "晴"},
                                                   ensure_ascii=False)}]})
        if s == 200:
            try:
                _tc2 = json.loads(b)["choices"][0]
            except Exception as e:
                _tc2_err = str(e)[:60]
        else:
            _tc2_err = f"HTTP {s} {b[:80]}"
    _final = ((_tc2 or {}).get("message") or {}).get("content") or ""
    check("14. tool_calls 多轮闭环",
          bool(_final.strip()) and (_tc2 or {}).get("finish_reason") == "stop",
          f"finish={(_tc2 or {}).get('finish_reason')} 回答长度={len(_final)} {_tc2_err}")

    print("=" * 68)
    passed = sum(1 for _, c, _ in results if c)
    print(f"通过 {passed}/{len(results)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
