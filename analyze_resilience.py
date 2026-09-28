#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按日志统计各 provider 的「抗压能力」，输出可直接用于 priority 排序的评分表。

抗压 = 大上下文装得下 + 失败率低 + 少限流 + 少超时。
纯标准库，只读 logs/usage.log 与 logs/fail.log。
"""
import json
import os
import re
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
USAGE = os.path.join(HERE, "logs", "usage.log")
FAIL = os.path.join(HERE, "logs", "fail.log")
CFG = os.path.join(HERE, "config", "providers.json")


def _iter_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def classify(err):
    e = err.lower()
    if "429" in e or "rate limit" in e or "tpm" in e or "rpm" in e:
        return "rate"
    if "timeout" in e or "timed out" in e:
        return "timeout"
    if "length" in e or "empty" in e:
        return "truncated"
    if "quota" in e or "exhaust" in e or "insufficient" in e or "balance" in e:
        return "quota"
    if "http 5" in e or "500" in e or "502" in e or "503" in e:
        return "server5xx"
    if "connect" in e or "ssl" in e or "reset" in e or "refused" in e:
        return "conn"
    if "http 4" in e:
        return "client4xx"
    return "other"


def main():
    ok = defaultdict(lambda: {"n": 0, "in": 0, "in_max": 0, "out": 0, "out_max": 0, "in_sum": 0})
    bad = defaultdict(lambda: defaultdict(int))
    bad_total = defaultdict(int)

    for r in _iter_jsonl(USAGE):
        p = r.get("provider") or "?"
        d = ok[p]
        d["n"] += 1
        i = int(r.get("in") or 0)
        o = int(r.get("out") or 0)
        d["in_sum"] += i
        d["in_max"] = max(d["in_max"], i)
        d["out_max"] = max(d["out_max"], o)

    for r in _iter_jsonl(FAIL):
        p = r.get("provider") or "?"
        k = classify(r.get("err") or "")
        bad[p][k] += 1
        bad_total[p] += 1

    cfg = {}
    if os.path.exists(CFG):
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)
    prov_cfg = {p["name"]: p for p in cfg.get("providers", [])}

    names = sorted(set(list(ok.keys()) + list(bad_total.keys())),
                   key=lambda n: (prov_cfg.get(n, {}).get("priority", 99), n))

    rows = []
    for n in names:
        d = ok[n]
        att = d["n"] + bad_total[n]
        sr = (d["n"] / att * 100) if att else 0.0
        rate = bad[n].get("rate", 0)
        tmo = bad[n].get("timeout", 0)
        cw = prov_cfg.get(n, {}).get("context_window")
        avg_in = int(d["in_sum"] / d["n"]) if d["n"] else 0
        rows.append({
            "name": n, "prio": prov_cfg.get(n, {}).get("priority"),
            "en": prov_cfg.get(n, {}).get("enabled"),
            "cw": cw, "ok": d["n"], "fail": bad_total[n], "att": att,
            "sr": sr, "in_max": d["in_max"], "avg_in": avg_in,
            "out_max": d["out_max"],
            "rate": rate, "tmo": tmo, "trunc": bad[n].get("truncated", 0),
            "quota": bad[n].get("quota", 0), "s5xx": bad[n].get("server5xx", 0),
            "conn": bad[n].get("conn", 0), "c4xx": bad[n].get("client4xx", 0),
            "other": bad[n].get("other", 0),
            "types": dict(bad[n]),
        })

    for r in rows:
        a = max(1, r["att"])
        r["rate_pct"] = r["rate"] / a * 100
        r["tmo_pct"] = r["tmo"] / a * 100
        r["trunc_pct"] = r["trunc"] / a * 100

    hdr = ("%-13s %4s %5s %8s %5s %5s %6s %7s %8s %7s %7s %7s"
           % ("provider", "prio", "en", "cw", "ok", "fail", "succ%", "in_max", "avg_in", "429%", "超时%", "截断%"))
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print("%-13s %4s %5s %8s %5d %5d %6.1f %7d %8d %6.1f%% %6.1f%% %6.1f%%"
              % (r["name"], r["prio"], "Y" if r["en"] else "n", r["cw"] or "-",
                 r["ok"], r["fail"], r["sr"], r["in_max"], r["avg_in"],
                 r["rate_pct"], r["tmo_pct"], r["trunc_pct"]))

    print()
    print("== 失败类型明细 ==")
    for r in rows:
        if r["fail"]:
            print("  %-13s %s" % (r["name"], json.dumps(r["types"], ensure_ascii=False)))

    # 综合抗压分：承载(40) + 成功率(35) + 抗限流(15) + 抗超时(10)
    print()
    print("== 抗压评分（用于 priority 排序）==")
    scored = []
    for r in rows:
        cap = min(1.0, (r["in_max"] or 0) / 200000.0)          # 实测扛住的最大输入
        cw_ok = min(1.0, (r["cw"] or 0) / 200000.0) if r["cw"] else 0.0
        cap = max(cap, cw_ok * 0.6)
        sr = r["sr"] / 100.0
        rl = 1.0 - min(1.0, r["rate_pct"] / 40.0)              # 429 率 40% 即归零
        to = 1.0 - min(1.0, r["tmo_pct"] / 25.0)               # 超时率 25% 即归零
        score = cap * 40 + sr * 35 + rl * 15 + to * 10
        scored.append((score, r))
    scored.sort(key=lambda x: -x[0])
    for i, (s, r) in enumerate(scored, 1):
        print("  %2d. %-13s 评分 %5.1f  承载%4.0f 成功%4.0f 抗限流%4.0f 抗超时%4.0f  (旧prio=%s, 样本%d)"
              % (i, r["name"], s,
                 min(1.0, max(r["in_max"] / 200000.0, (r["cw"] or 0) / 200000.0 * 0.6)) * 40,
                 r["sr"] / 100.0 * 35,
                 (1.0 - min(1.0, r["rate_pct"] / 40.0)) * 15,
                 (1.0 - min(1.0, r["tmo_pct"] / 25.0)) * 10,
                 r["prio"], r["att"]))
    print()
    print("  注：样本 <20 的源（antigravity / volcengine / modelscope / siliconflow）评分仅供参考，")
    print("      需结合窗口配置与上游模型档次人工定档。")

    # 建议 priority 映射
    print()
    print("== 建议 priority ==")
    out = []
    for i, (s, r) in enumerate(scored, 1):
        out.append((r["name"], i))
        print("  %-13s -> %d" % (r["name"], i))
    print()
    print(json.dumps(dict(out), ensure_ascii=False))


if __name__ == "__main__":
    main()
