#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Watchdog —— 根治"Agent 静默中断"。
轮询各 Agent 的心跳文件；超过阈值没更新 = 判定它卡死/挂了，立刻在飞书群报警。
用法：python watchdog.py            # 查一轮退出
      python watchdog.py --loop 60  # 常驻每60s查一轮

心跳文件约定：每个执行中任务必须每 N 秒覆盖写一次心跳，结束写 DONE。
任何报错/中断，必须立刻写一行 ERROR: <原因> 到心跳文件。
"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import json, os, time, datetime, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.join(HERE, "config", "watchdog_registry.json")
LARK = os.environ.get("LARK_CLI", "lark-cli.exe")
CHAT = "oc_d0d2cfc3166156cf82fc057fb5cb7b1b"

def load_reg():
    with open(REGISTRY, "r", encoding="utf-8") as f:
        return json.load(f)

def send_group(text):
    try:
        msgfile = os.path.join(HERE, "logs", "_watchdog_msg.md")
        with open(msgfile, "w", encoding="utf-8") as f:
            f.write(text)
        subprocess.run([LARK, "im", "+messages-send", "--chat-id", CHAT,
                        "--markdown", text, "--as", "user"],
                       capture_output=True, timeout=30)
        print(f"[watchdog] alerted group: {text[:80]}", flush=True)
    except Exception as e:
        print(f"[watchdog] send failed: {e}", flush=True)

def check_once():
    reg = load_reg()
    now = time.time()
    for agent in reg["agents"]:
        path = agent["heartbeat_file"]
        max_age = agent["max_age_sec"]
        if not os.path.exists(path):
            # 文件不存在 = 任务还没开始；若 expected_running 才报警
            if agent.get("expected_running"):
                send_group(f"[WATCHDOG-ALERT] {agent['name']} 心跳文件不存在但应在运行：{path}")
            continue
        mtime = os.path.getmtime(path)
        age = now - mtime
        content = open(path, encoding="utf-8", errors="ignore").read().strip()
        if "DONE" in content:
            continue
        if "ERROR" in content:
            send_group(f"[WATCHDOG-ALERT] {agent['name']} 上报错误：{content[:200]}")
            continue
        if age > max_age:
            send_group(f"[WATCHDOG-ALERT] {agent['name']} 心跳已 {int(age)}s 未更新（阈值 {max_age}s），疑似卡死/静默中断。最后内容：{content[:150]}")

def main():
    if "--loop" in sys.argv:
        i = sys.argv.index("--loop")
        interval = int(sys.argv[i+1]) if i+1 < len(sys.argv) else 60
        while True:
            check_once()
            time.sleep(interval)
    else:
        check_once()

if __name__ == "__main__":
    main()
