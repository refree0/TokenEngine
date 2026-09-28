"""把火山方舟（Ark）接入 TokenEngine 轮换名单。

实测（2026-09-26）：key 有效，账号已开通模型服务；
135 个模型中 37 个无状态，其中 14 个文本模型实测可调用、7 个支持图片输入。
Retiring / Shutdown 状态模型全部返回 InvalidEndpointOrModel.NotFound，不可用。

用法：先设置环境变量 ARK_API_KEY（火山方舟控制台 -> API Key），再执行
    python add_volcengine.py [--priority N]
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

CFG = Path(__file__).resolve().parent / "config" / "providers.json"

# 不要把 key 写进代码：从环境变量读取。
#   Windows : set ARK_API_KEY=ark-xxxx
#   Linux/Mac: export ARK_API_KEY=ark-xxxx
ARK_KEY = os.environ.get("ARK_API_KEY", "").strip()

# 实测可调用，按「编码能力」从强到弱排序；auto 是源内 fallback 链，
# 排前面的先用，额度耗尽/报错自动往后走。
TEXT_CHAIN = [
    "doubao-seed-2-1-pro-260915",      # 最新旗舰，视觉+文本
    "deepseek-v4-pro-ga-260813",
    "glm-5-3-flash-260828",            # 视觉可用，速度快
    "doubao-seed-2-0-code-preview-260215",  # 代码专用预览
    "doubao-seed-2-1-pro-260628",
    "deepseek-v4-1-flash-260910",
    "glm-5-2-260617",
    "doubao-seed-2-1-turbo-260628",
    "deepseek-v4-flash-ga-260731",
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-0-lite-260428",
    "doubao-seed-2-0-mini-260428",
    "doubao-seed-character-260628",    # 角色扮演向，仅兜底
    "doubao-seed-character-251128",
]

# 实测支持 image_url 输入的
VISION_CHAIN = [
    "doubao-seed-2-1-pro-260915",
    "glm-5-3-flash-260828",
    "doubao-seed-2-1-pro-260628",
    "doubao-seed-2-1-lite-260915",
    "doubao-seed-2-1-turbo-260628",
    "doubao-seed-2-0-lite-260428",
    "doubao-seed-2-0-mini-260428",
]

NOTE = (
    "火山方舟 Ark（ark.cn-beijing.volces.com/api/v3）。2026-09-26 实测：账号已开通模型服务，"
    "14 个文本模型可调用（另 7 个支持图片输入）。全量 135 个模型里 69 个 Shutdown、29 个 Retiring 已全部失效。"
    "注意：本源是「安心体验」免费额度，按模型独立发放、用完即停不扣费，属一次性资源，跑完不会恢复；"
    "要扩容就加账号（往 accounts 追加 {\"name\":\"火山N\",\"api_key\":\"ark-...\"}），额度按账号独立。"
    "priority 可随时调：想让火山先用就调小，想留作储备就调大。"
)


def main() -> int:
    if not ARK_KEY:
        print("未设置环境变量 ARK_API_KEY，请先从火山方舟控制台获取 API Key 再运行。",
              file=sys.stderr)
        return 2

    priority = 3
    if "--priority" in sys.argv:
        priority = int(sys.argv[sys.argv.index("--priority") + 1])

    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    providers: list[dict] = cfg["providers"]

    if any(p.get("name") == "volcengine" for p in providers):
        print("已存在 volcengine，先移除再重建")
        providers = [p for p in providers if p.get("name") != "volcengine"]
        cfg["providers"] = providers

    # 让出 priority 槽位：>= priority 的原有源统一后移 1
    for p in providers:
        if isinstance(p.get("priority"), int) and p["priority"] >= priority:
            p["priority"] += 1

    model_map: dict = {"auto": TEXT_CHAIN, "vision": VISION_CHAIN}
    # 便捷别名：统一短名 -> 具体版本
    aliases = {
        "doubao-seed-2-1-pro": "doubao-seed-2-1-pro-260915",
        "doubao-code": "doubao-seed-2-0-code-preview-260215",
        "glm-5.3-flash": "glm-5-3-flash-260828",
    }
    model_map.update(aliases)

    entry = {
        "name": "volcengine",
        "enabled": True,
        "priority": priority,
        "context_window": 262144,
        "timeout": 60,
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "accounts": [{"name": "火山1", "api_key": ARK_KEY}],
        "models_out": TEXT_CHAIN,
        "model_map": model_map,
        "cooldown": {"on_429_sec": 20, "on_5xx_sec": 8},
        "_note": NOTE,
    }

    # 按 priority 排好序插入
    providers.append(entry)
    providers.sort(key=lambda p: p.get("priority", 999))
    cfg["providers"] = providers

    bak = CFG.with_suffix(".json.bak_%s" % time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(CFG, bak)
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("备份 ->", bak.name)
    print("已写入 volcengine，priority=%d" % priority)
    print("当前优先级顺序：")
    for p in providers:
        print("  %d  %-12s enabled=%s" % (p.get("priority", 999), p["name"], p.get("enabled")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
