# -*- coding: utf-8 -*-
"""
TokenEngine 模型能力测试 harness。
通过 Codex 真实的 /v1/responses 路径，对每个源的主要模型逐一测试：
  T1 身份诚实   T2 数学推理   T3 代码(可执行验证)
  T4 工具调用    T5 工具结果多轮续答   T6 视觉识图(仅视觉模型)
结果写 logs/model_test_results.json，并实时打印进度。串行，避免限流。
"""
import json, os, re, time, base64, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
RESP_URL = "http://127.0.0.1:8317/v1/responses"
RESULT = os.path.join(HERE, "logs", "model_test_results.json")
IMG = os.path.join(HERE, "logs", "vision_test_card.png")

# (显式路由名, 是否视觉模型)
MODELS = [
    ("sensenova:deepseek-v4-flash", False),
    ("sensenova:kimi-k3", False),
    ("sensenova:glm-5.2", False),
    ("bigmodel:glm-4.5-flash", False),
    ("bigmodel:glm-5.2", False),
    ("bigmodel:glm-4v-flash", True),
    ("amd:DeepSeek-V4-Flash", False),
    ("amd:DeepSeek-V4-Flash-Vision-Exp", True),
    ("amd:Qwen3.8-27B", True),
    ("amd:Qwen3.8-Flash-Next", False),
    ("amd:MiMo-V2.6-Flash", False),
    ("amd:MiniCPM5-2B", False),
    ("bailian:qwen3.5-flash", False),
    ("bailian:qwen3-vl-flash", True),
    ("bailian:qwen3-vl-235b-a22b-instruct", True),
    ("bailian:qwen-vl-max", True),
    ("bailian:qwen3.5-ocr", True),
    ("aihubmix:coding-glm-5.3-free", False),
    ("siliconflow:Qwen/Qwen3.5-27B", False),
]

STOCK_TOOL = {
    "type": "function", "name": "get_stock_price",
    "description": "查询某只A股股票的最新价格",
    "parameters": {"type": "object",
                   "properties": {"symbol": {"type": "string", "description": "股票代码，如 600519"}},
                   "required": ["symbol"]}}


def make_test_image():
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (960, 560), "white")
    d = ImageDraw.Draw(img)
    try:
        from PIL import ImageFont
        f1 = ImageFont.truetype("C:\\Windows\\Fonts\\arialbd.ttf", 46)
        f2 = ImageFont.truetype("C:\\Windows\\Fonts\\arial.ttf", 40)
    except Exception:
        f1 = f2 = None
    d.rectangle([18, 18, 942, 542], outline="black", width=4)
    d.text((50, 60), "Vision Test Card", fill="black", font=f1)
    d.text((50, 180), "Code Number: 7392", fill="black", font=f2)
    d.text((50, 290), "Keyword: Quant Research", fill="black", font=f2)
    d.text((50, 400), "ABC-XYZ 2026", fill="black", font=f2)
    img.save(IMG)


def responses_call(model, inp, tools=None, max_tokens=1024, timeout=90, retries=3):
    """连接层/限流重试：只针对断连、超时、429/5xx，不改变模型能力结果。"""
    last = None
    for k in range(retries):
        try:
            return _responses_call_once(model, inp, tools, max_tokens, timeout)
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(5 * (k + 1)); continue
            raise
        except Exception as e:
            last = e
            msg = str(e).lower()
            if ("remote end closed" in msg) or ("timed out" in msg) \
                    or isinstance(e, (ConnectionError, OSError)):
                time.sleep(4 * (k + 1)); continue
            raise
    raise last


def _responses_call_once(model, inp, tools=None, max_tokens=1024, timeout=90):
    body = {"model": model, "input": inp, "max_output_tokens": max_tokens, "stream": True}
    if tools:
        body["tools"] = tools
    data = json.dumps(body).encode("utf-8")
    rq = urllib.request.Request(RESP_URL, data=data,
                                headers={"Content-Type": "application/json"})
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    t0 = time.time()
    with op.open(rq, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
        prov = resp.headers.get("x-tokenengine-provider")
        real = resp.headers.get("x-tokenengine-model")
    ms = (time.time() - t0) * 1000
    text, tcs = "", {}

    def get(iid):
        return tcs.setdefault(iid, {"name": "", "args": ""})

    ev = None
    for line in raw.split("\n"):
        if line.startswith("event: "):
            ev = line[7:].strip()
        elif line.startswith("data: "):
            try:
                d = json.loads(line[6:])
            except Exception:
                continue
            if ev == "response.output_text.delta":
                text += d.get("delta", "")
            elif ev == "response.function_call_arguments.delta":
                get(d.get("item_id"))["args"] += d.get("delta", "")
            elif ev == "response.output_item.added":
                it = d.get("item", {})
                if it.get("type") == "function_call":
                    cid = it.get("call_id") or it.get("id")
                    get(cid)["name"] = it.get("name", "")
    tool_calls = list(tcs.values())
    return {"text": text, "tool_calls": tool_calls, "ms": ms,
            "provider": prov, "real": real}


def user_msg(text):
    return [{"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": text}]}]


def check_code(text):
    m = re.search(r"```(?:python)?\s*(.*?)```", text, re.S)
    code = m.group(1) if m else text
    try:
        ns = {}
        exec(code, ns)
        f = ns.get("is_prime")
        if not callable(f):
            return False, "no is_prime()"
        ok = (f(29) is True and f(30) is False and f(1) is False and f(2) is True)
        return bool(ok), ""
    except Exception as e:
        return False, type(e).__name__ + ": " + str(e)[:120]


def run_one(model, is_vision):
    r = {"model": model, "provider": None, "real": None, "tests": {}}

    def rec(tid, ok, note, extra=None):
        e = {"pass": bool(ok), "note": note}
        if extra:
            e.update(extra)
        r["tests"][tid] = e
        print(f"    {tid}: {'PASS' if ok else 'FAIL'} {note}")

    # T1 身份
    try:
        o = responses_call(model, user_msg(
            "请如实回答：你是哪个公司的哪个模型、什么版本？若不能确定就直接说“我不确定”，"
            "不要猜测，也不要自称是其他公司（如 OpenAI）的模型。"))
        r["provider"], r["real"], t = o["provider"], o["real"], o["text"]
        low = t.lower()
        if (("gpt" in low and ("openai" in low or "astra" in low)) or "gpt-6" in low
                or ("我是gpt" in low.replace(" ", ""))):
            rec("T1", False, "冒充 OpenAI/GPT 模型", {"raw": t[:300]})
        elif any(k in low for k in ["deepseek", "kimi", "glm", "qwen", "mimo", "minicpm"]) \
                or any(k in t for k in ["商汤", "智谱", "阿里", "深度求索", "月之暗面", "面壁", "不确定"]):
            rec("T1", True, "身份基本诚实", {"raw": t[:200]})
        else:
            rec("T1", False, "未给出可信身份", {"raw": t[:200]})
    except Exception as e:
        rec("T1", False, type(e).__name__ + " " + str(e)[:100])

    # T2 推理
    try:
        q = ("水池有甲、乙两个进水管：甲单开6小时注满，乙单开12小时注满；底部排水管单开8小时排空满池。"
             "三管同开，多少小时注满空池？给出过程与答案。")
        o = responses_call(model, user_msg(q))
        t = o["text"]
        ok = bool(re.search(r"8\s*小时", t))
        rec("T2", ok, "答案应为8小时", {"raw": t[:300]})
    except Exception as e:
        rec("T2", False, type(e).__name__ + " " + str(e)[:100])

    # T3 代码
    try:
        q = ("用 Python 写函数 is_prime(n) 判断正整数 n 是否为素数，正确处理 n<2；"
             "并给出 is_prime(29) 和 is_prime(30) 的结果。只输出代码。")
        o = responses_call(model, user_msg(q))
        t = o["text"]
        ok, err = check_code(t)
        rec("T3", ok, "代码可执行且结果正确" if ok else "代码验证失败 " + err, {"raw": t[:400]})
    except Exception as e:
        rec("T3", False, type(e).__name__ + " " + str(e)[:100])

    # T4 工具调用
    try:
        o = responses_call(model, user_msg("帮我查贵州茅台（股票代码 600519）现在的股价。"),
                           tools=[STOCK_TOOL])
        tcs = o["tool_calls"]
        good = [c for c in tcs if c["name"] == "get_stock_price" and "600519" in c["args"]]
        rec("T4", bool(good),
            "正确发起工具调用" if good else "未正确调用工具",
            {"tool_calls": tcs, "raw": o["text"][:150]})
    except Exception as e:
        rec("T4", False, type(e).__name__ + " " + str(e)[:100])

    # T5 工具结果多轮续答
    try:
        inp = [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "查贵州茅台（600519）股价。"}]},
            {"type": "function_call", "call_id": "c1", "name": "get_stock_price",
             "arguments": '{"symbol": "600519"}'},
            {"type": "function_call_output", "call_id": "c1",
             "output": '{"price": 1499.5, "currency": "CNY"}'},
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "它比 1500 元贵还是便宜？差多少？"}]},
        ]
        o = responses_call(model, inp, tools=[STOCK_TOOL])
        t = o["text"]
        ok = ("便宜" in t or "低于" in t) and ("0.5" in t or "0.50" in t)
        rec("T5", ok, "能基于工具结果续答" if ok else "未正确使用工具结果", {"raw": t[:300]})
    except Exception as e:
        rec("T5", False, type(e).__name__ + " " + str(e)[:100])

    # T6 视觉
    if is_vision:
        try:
            b = base64.b64encode(open(IMG, "rb").read()).decode()
            url = "data:image/png;base64," + b
            inp = [{"type": "message", "role": "user", "content": [
                {"type": "input_image", "image_url": {"url": url}},
                {"type": "input_text", "text": "请逐字读出图中的文字和数字，不要遗漏。"}]}]
            o = responses_call(model, inp, max_tokens=800)
            t = o["text"]
            leaked = ("<|observation|>" in t) or ("<resource" in t) or ("</think>" in t)
            num = "7392" in t
            kw = "quant" in t.lower() or "research" in t.lower()
            ok = (not leaked) and num and kw
            rec("T6", ok,
                f"识图正常(数字7392={'有' if num else '无'},关键词={'有' if kw else '无'},泄漏标记={'有' if leaked else '无'})",
                {"raw": t[:400]})
        except Exception as e:
            rec("T6", False, type(e).__name__ + " " + str(e)[:100])
    return r


def main():
    make_test_image()
    results = []
    for i, (model, is_vision) in enumerate(MODELS, 1):
        print(f"[{i}/{len(MODELS)}] {model}")
        try:
            results.append(run_one(model, is_vision))
        except Exception as e:
            print("    MODEL ERROR " + str(e))
            results.append({"model": model, "error": str(e), "tests": {}})
        with open(RESULT, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1)
        time.sleep(4)
    print("DONE -> " + RESULT)


if __name__ == "__main__":
    main()
