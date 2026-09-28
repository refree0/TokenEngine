#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量探活阿里云百炼的 chat 类模型，找出真正可用的（有免费额度/已开通）。

用法::

    python probe_bailian_models.py            # 全量探活（支持断点续测）
    python probe_bailian_models.py --summary  # 只看已有结果摘要

结果写入 logs/bailian_probe_result.json，可反复运行（已测过的会跳过）。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config", "providers.json")
CAND = os.path.join(HERE, "logs", "bailian_chat_candidates.json")
OUT = os.path.join(HERE, "logs", "bailian_probe_result.json")
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


def load_key():
    d = json.load(open(CFG, encoding="utf-8"))
    for p in d["providers"]:
        if p["name"] == "bailian":
            return p["api_key"]
    raise SystemExit("找不到 bailian provider")


def probe(model, key, timeout=20):
    """返回 (status, err_snippet)。200 = 可用。"""
    body = json.dumps({"model": model,
                       "messages": [{"role": "user", "content": "hi"}],
                       "max_tokens": 5}).encode()
    req = urllib.request.Request(URL, data=body,
                                 headers={"Authorization": "Bearer " + key,
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:160]
    except Exception as e:
        return "ERR", str(e)[:100]


def summarize(res):
    ok = [m for m, v in res.items() if v.get("status") == 200]
    fail = {m: v for m, v in res.items() if v.get("status") != 200}
    print(f"  已测 {len(res)} 个：可用 {len(ok)}，不可用 {len(fail)}")
    if fail:
        from collections import Counter
        c = Counter(str(v.get("status")) for v in fail.values())
        print(f"  失败状态分布: {dict(c)}")
    return ok, fail


def main():
    key = load_key()
    models = json.load(open(CAND, encoding="utf-8"))
    res = {}
    if os.path.exists(OUT):
        try:
            res = json.load(open(OUT, encoding="utf-8"))
        except Exception:
            res = {}

    if "--summary" in sys.argv:
        ok, _ = summarize(res)
        print("\n可用模型:")
        for m in ok:
            print("  ", m)
        return

    todo = [m for m in models if m not in res]
    print(f"候选 {len(models)} 个，待测 {len(todo)} 个")
    print("=" * 66)

    for i, m in enumerate(todo, 1):
        st, err = probe(m, key)
        res[m] = {"status": st, "err": err, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
        mark = "✅" if st == 200 else "❌"
        print(f"{mark} [{i}/{len(todo)}] {st}  {m}")
        # 每 10 个存一次盘（防中断丢进度）
        if i % 10 == 0:
            json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        time.sleep(0.35)  # 轻微延迟，避免触发平台限流

    json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("=" * 66)
    ok, _ = summarize(res)
    print(f"\n✅ 结果已保存: {OUT}")
    print(f"\n可用模型清单（{len(ok)} 个）:")
    for m in ok:
        print("  ", m)


if __name__ == "__main__":
    main()
