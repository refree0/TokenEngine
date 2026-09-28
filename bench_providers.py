"""各免费源耐用性压测：成功率 / 延迟 / 错误类型 / 视觉可用性。

用法：python bench_providers.py [--text N] [--vision N] [--only provider]
结果写入 logs/bench_result.json
"""
from __future__ import annotations

import argparse
import base64
import json
import struct
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

GATEWAY = "http://127.0.0.1:8317/v1/chat/completions"
PROVIDERS = ["sensenova", "bigmodel", "volcengine", "amd", "bailian", "siliconflow"]

PROMPT = ("用一句话解释什么是幂等性，再举一个 HTTP 方法上的例子。"
          "要求：中文，不超过 80 字。")


def make_png(w: int, h: int, rgb: tuple) -> str:
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))

    def chunk(t: bytes, d: bytes) -> bytes:
        c = t + d
        return struct.pack(">I", len(d)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
    return "data:image/png;base64," + base64.b64encode(png).decode()


IMG = make_png(128, 128, (0, 128, 255))


def call(model: str, messages: list, max_tokens: int = 80, timeout: int = 70):
    body = json.dumps({"model": model, "messages": messages,
                       "max_tokens": max_tokens}).encode()
    req = urllib.request.Request(GATEWAY, data=body, headers={
        "Authorization": "Bearer x", "Content-Type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        txt = (d.get("choices", [{}])[0].get("message", {}).get("content") or "")
        if not txt.strip():
            return {"ok": False, "ms": int((time.time() - t0) * 1000),
                    "model": d.get("model"), "err": "空内容"}
        return {"ok": True, "ms": int((time.time() - t0) * 1000),
                "model": d.get("model"), "err": ""}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        code = ""
        try:
            code = (json.loads(raw).get("error") or {}).get("message", "")[:90]
        except Exception:
            code = raw[:90]
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "model": None, "err": f"HTTP{e.code} {code}"}
    except Exception as e:
        return {"ok": False, "ms": int((time.time() - t0) * 1000),
                "model": None, "err": type(e).__name__}


def bench(provider: str, n_text: int, n_vision: int) -> dict:
    out = {"text": [], "vision": []}
    for _ in range(n_text):
        out["text"].append(call(f"{provider}:auto",
                                [{"role": "user", "content": PROMPT}]))
    for _ in range(n_vision):
        out["vision"].append(call(f"{provider}:vision", [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": IMG}},
            {"type": "text", "text": "只答这张图的颜色"}]}], max_tokens=20))
    return out


def summarize(rows: list) -> dict:
    if not rows:
        return {"n": 0, "ok": 0, "rate": 0.0, "ms": 0, "models": [], "errs": []}
    ok = [r for r in rows if r["ok"]]
    lats = sorted(r["ms"] for r in ok)
    return {
        "n": len(rows), "ok": len(ok),
        "rate": round(len(ok) / len(rows), 3),
        "ms_p50": lats[len(lats) // 2] if lats else 0,
        "ms_max": lats[-1] if lats else 0,
        "models": sorted({r["model"] for r in ok if r["model"]}),
        "errs": sorted({r["err"][:70] for r in rows if not r["ok"]}),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", type=int, default=5)
    ap.add_argument("--vision", type=int, default=2)
    ap.add_argument("--only", default=None)
    a = ap.parse_args()

    targets = [a.only] if a.only else PROVIDERS
    result = {}
    for p in targets:
        print(f"--- {p} 压测中 (text x{a.text}, vision x{a.vision}) ...", flush=True)
        raw = bench(p, a.text, a.vision)
        s = {"text": summarize(raw["text"]), "vision": summarize(raw["vision"]), "raw": raw}
        result[p] = s
        t, v = s["text"], s["vision"]
        print(f"    文本 {t['ok']}/{t['n']}  p50={t['ms_p50']}ms  模型={t['models']}")
        if v["n"]:
            print(f"    视觉 {v['ok']}/{v['n']}  p50={v['ms_p50']}ms  模型={v['models']}")
        for e in t["errs"][:2]:
            print(f"    文本错误: {e}")
        for e in v["errs"][:2]:
            print(f"    视觉错误: {e}")

    out = Path(__file__).resolve().parent / "logs" / "bench_result.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n结果已写入", out)

    print("\n================ 汇总（按文本成功率→延迟排序）================")
    print("%-12s %10s %8s %10s %10s" % ("provider", "文本成功率", "p50", "视觉成功率", "p50"))
    def key(kv):
        s = kv[1]["text"]
        return (-s["rate"], s["ms_p50"] or 99999)
    for name, s in sorted(result.items(), key=key):
        t, v = s["text"], s["vision"]
        print("%-12s %6d/%-3d %7dms %6d/%-3d %8dms" % (
            name, t["ok"], t["n"], t["ms_p50"], v["ok"], v["n"], v["ms_p50"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
