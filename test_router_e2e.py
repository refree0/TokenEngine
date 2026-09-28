# -*- coding: utf-8 -*-
"""端到端验证：经 router 模拟 Codex / Chat 客户端的真实请求形态。"""
import json, time, urllib.request, urllib.error

BASE = "http://127.0.0.1:8317/v1"

def post(path, payload, timeout=120, headers=None):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    rq = urllib.request.Request(BASE + path, data=data, method="POST")
    rq.add_header("Content-Type", "application/json")
    rq.add_header("Authorization", "Bearer any")
    for k, v in (headers or {}).items():
        rq.add_header(k, v)
    t0 = time.time()
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as resp:
            body = resp.read()
            return resp.status, dict(resp.headers), body.decode("utf-8", "ignore"), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read().decode("utf-8", "ignore"), time.time() - t0

def t1_chat_short():
    print("[T1] chat 非流式短请求 auto ...", flush=True)
    st, hdr, body, dt = post("/chat/completions", {
        "model": "auto", "max_tokens": 64,
        "messages": [{"role": "user", "content": "只回复四个字：链路正常"}]})
    ok = False
    if st == 200:
        j = json.loads(body)
        txt = j["choices"][0]["message"].get("content", "")
        ok = bool(txt.strip())
        print(f"     {dt:5.1f}s provider={hdr.get('x-tokenengine-provider')} model={hdr.get('x-tokenengine-model')} text={txt[:40]!r}")
    else:
        print(f"     FAIL status={st} body={body[:300]}")
    return ok

def t2_chat_stream():
    print("[T2] chat 流式短请求（WorkBuddy 形态）...", flush=True)
    st, hdr, body, dt = post("/chat/completions", {
        "model": "auto", "max_tokens": 64, "stream": True,
        "messages": [{"role": "user", "content": "只回复四个字：流式正常"}]})
    ok = st == 200 and "chat.completion.chunk" in body and "[DONE]" in body
    print(f"     {dt:5.1f}s status={st} provider={hdr.get('x-tokenengine-provider')} chunks={'chat.completion.chunk' in body} DONE={'[DONE]' in body} len={len(body)}")
    return ok

def t3_responses_short():
    print("[T3] responses SSE 短请求（Codex 形态）...", flush=True)
    st, hdr, body, dt = post("/responses", {
        "model": "gpt-5", "max_output_tokens": 64, "stream": True,
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "只回复四个字：响应正常"}]}]},
        headers={"Accept": "text/event-stream"})
    ok = st == 200 and "response.completed" in body
    # 提取最终文本
    text = ""
    for line in body.splitlines():
        if line.startswith("data:") and "output_text.delta" in line:
            try:
                text += json.loads(line[5:].strip())["delta"]
            except Exception:
                pass
    print(f"     {dt:5.1f}s status={st} provider={hdr.get('x-tokenengine-provider')} completed={'response.completed' in body} text={text[:40]!r}")
    return ok

def t4_responses_long():
    print("[T4] responses 长上下文 ~20k tokens（验证跳过 bigmodel 16k 限制）...", flush=True)
    filler = "The quick brown fox jumps over the lazy dog. " * 1600
    st, hdr, body, dt = post("/responses", {
        "model": "gpt-5", "max_output_tokens": 64, "stream": True,
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": filler + "\n只回复四个字：长文正常"}]}]},
        headers={"Accept": "text/event-stream"}, timeout=150)
    ok = st == 200 and "response.completed" in body
    prov = hdr.get("x-tokenengine-provider")
    print(f"     {dt:5.1f}s status={st} provider={prov} model={hdr.get('x-tokenengine-model')} completed={'response.completed' in body}")
    if st != 200:
        print(f"     body={body[:300]}")
    if prov == "bigmodel":
        print("     [WARN] 长上下文仍落到 bigmodel，窗口感知未生效！")
        ok = False
    return ok

def t5_responses_tools():
    print("[T5] responses 带工具定义（Codex 真实形态，tools 体积大）...", flush=True)
    tools = [{"type": "function", "name": "shell",
              "description": "Execute a shell command on the local machine",
              "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                             "required": ["command"]}}]
    st, hdr, body, dt = post("/responses", {
        "model": "gpt-5", "max_output_tokens": 256, "stream": True,
        "tools": tools,
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "现在几点了？用一句话回答，不要调用工具。"}]}]},
        headers={"Accept": "text/event-stream"}, timeout=120)
    ok = st == 200 and "response.completed" in body
    has_fc = "function_call" in body
    text = ""
    for line in body.splitlines():
        if line.startswith("data:") and "output_text.delta" in line:
            try:
                text += json.loads(line[5:].strip())["delta"]
            except Exception:
                pass
    print(f"     {dt:5.1f}s status={st} provider={hdr.get('x-tokenengine-provider')} text={text[:50]!r} function_call={has_fc}")
    return ok

def t6_cooldown():
    print("[T6] 连续 5 次短请求（验证熔断冷却下仍稳定成功、不中断）...", flush=True)
    wins = 0
    for i in range(5):
        st, hdr, body, dt = post("/chat/completions", {
            "model": "auto", "max_tokens": 32,
            "messages": [{"role": "user", "content": f"回复数字 {i+1}"}]}, timeout=90)
        if st == 200:
            j = json.loads(body)
            txt = j["choices"][0]["message"].get("content", "")
            if txt.strip():
                wins += 1
            print(f"     #{i+1} {dt:5.1f}s {hdr.get('x-tokenengine-provider'):11s} {txt[:20]!r}")
        else:
            print(f"     #{i+1} FAIL {st} {body[:150]}")
        time.sleep(2)
    print(f"     成功 {wins}/5")
    return wins == 5

if __name__ == "__main__":
    results = []
    for fn in (t1_chat_short, t2_chat_stream, t3_responses_short, t4_responses_long,
               t5_responses_tools, t6_cooldown):
        try:
            results.append((fn.__name__, fn()))
        except Exception as e:
            import traceback; traceback.print_exc()
            results.append((fn.__name__, False))
        print()
    print("=" * 60)
    for name, ok in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("=" * 60)
