#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给 TokenEngine 的某个 provider 添加账号（写入 config/providers.json 的 accounts[]）。

用法::

    # 交互式（推荐，会提示输入）
    python add_account.py sensenova

    # 命令行直接给
    python add_account.py sensenova 商汤2 sk-xxxxxxxxxxxx

    # 查看当前有哪些账号
    python add_account.py sensenova --list

    # 删除某个账号（按名称）
    python add_account.py sensenova --remove 商汤2

改完立即生效（router 每次请求重新读配置，账号池检测到指纹变化会自动重建），
无需重启。脚本会自动备份 providers.json。
"""
import json
import os
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "config", "providers.json")


def load():
    with open(CFG, "r", encoding="utf-8") as f:
        return json.load(f)


def save(cfg):
    base = f"{CFG}.bak_{time.strftime('%Y%m%d_%H%M%S')}"
    bak = base
    i = 1
    while os.path.exists(bak):  # 同一秒内多次操作时避免覆盖
        bak = f"{base}_{i}"
        i += 1
    shutil.copy2(CFG, bak)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return bak


def mask(key):
    if not key:
        return "***"
    return f"{key[:6]}...{key[-4:]}" if len(key) > 14 else key[:2] + "*" * (len(key) - 4) + key[-2:]


def find_provider(cfg, name):
    for p in cfg.get("providers", []):
        if p.get("name") == name:
            return p
    return None


def show_accounts(p):
    accounts = p.get("accounts")
    if not accounts:
        print(f"  （{p['name']} 目前是单 key 模式，key = {mask(p.get('api_key'))}）")
        print(f"  提示：添加第一个账号后会自动转为账号池模式。")
        return
    print(f"  {p['name']} 共 {len(accounts)} 个账号：")
    for i, a in enumerate(accounts, 1):
        print(f"    {i}. {a.get('name'):12s} key={mask(a.get('api_key'))}  "
              f"并发={a.get('max_concurrency', 4)}")


def main():
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        sys.exit(0)

    provider_name = args[0]
    cfg = load()
    p = find_provider(cfg, provider_name)
    if p is None:
        names = [x.get("name") for x in cfg.get("providers", [])]
        print(f"找不到 provider：{provider_name}")
        print(f"现有：{', '.join(names)}")
        sys.exit(1)

    if "--list" in args:
        show_accounts(p)
        return

    if "--remove" in args:
        idx = args.index("--remove")
        if idx + 1 >= len(args):
            print("用法：--remove <账号名>")
            sys.exit(1)
        target = args[idx + 1]
        accounts = p.get("accounts") or []
        before = len(accounts)
        p["accounts"] = [a for a in accounts if a.get("name") != target]
        if len(p["accounts"]) == before:
            print(f"没找到账号：{target}")
            sys.exit(1)
        bak = save(cfg)
        print(f"✅ 已删除账号 {target}（备份：{os.path.basename(bak)}）")
        show_accounts(p)
        return

    # 添加账号
    if len(args) >= 3:
        acc_name, api_key = args[1], args[2]
    else:
        print(f"给 {provider_name} 添加账号（直接回车可跳过/取消）")
        default_name = f"{provider_name}{len(p.get('accounts') or []) + 1}"
        acc_name = input(f"  账号名称 [{default_name}]: ").strip() or default_name
        api_key = input("  API Key: ").strip()
    if not api_key:
        print("未提供 API Key，已取消。")
        sys.exit(1)

    accounts = p.get("accounts")
    if not accounts:
        # 首次添加：把现有单 key 作为账号1 迁移进来
        accounts = []
        old = p.get("api_key")
        if old:
            accounts.append({"name": f"{provider_name}1", "api_key": old, "max_concurrency": 4})
            print(f"  （已把现有 api_key 迁移为账号「{provider_name}1」）")
    if any(a.get("name") == acc_name for a in accounts):
        print(f"账号名 {acc_name} 已存在，请换一个名字。")
        sys.exit(1)
    accounts.append({"name": acc_name, "api_key": api_key, "max_concurrency": 4})
    p["accounts"] = accounts
    p.setdefault("cooldown", {"base": 60, "factor": 1.5, "max": 120, "jitter": 0.5})

    bak = save(cfg)
    print(f"✅ 已添加账号「{acc_name}」（备份：{os.path.basename(bak)}）")
    print("   配置热加载，立即生效，无需重启 router。")
    print()
    show_accounts(p)
    print()
    print("提示：可访问 http://127.0.0.1:8317/health 查看账号池实时状态。")


if __name__ == "__main__":
    main()
