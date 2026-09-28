#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""读取 logs/usage.log，按 总计 / 源 / 日期 / 模型 汇总 token 消耗。"""
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import json, collections

PATH = os.path.join(_ROOT, "logs", "usage.log")

tot = collections.Counter()
by_prov = collections.Counter()
by_day = collections.Counter()
by_model = collections.Counter()
n = 0

try:
    for line in open(PATH, encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        n += 1
        i, o, t = r.get('in', 0), r.get('out', 0), r.get('total', 0)
        tot['in'] += i; tot['out'] += o; tot['total'] += t
        by_prov[r.get('provider', '?')] += t
        by_day[r.get('ts', '')[:10]] += t
        by_model[r.get('model', '?')] += t
except FileNotFoundError:
    print('还没有用量记录（logs/usage.log 不存在）。')
    raise SystemExit

if n == 0:
    print('还没有用量记录。')
    raise SystemExit

print('请求次数 =', n)
print('总 token ：输入(in) = {0:,}  输出(out) = {1:,}  合计 = {2:,}'.format(tot['in'], tot['out'], tot['total']))
print('\n--- 按免费源 ---')
for k, v in by_prov.most_common():
    print('{0:12} {1:>12,}'.format(k, v))
print('\n--- 按日期 ---')
for k, v in sorted(by_day.items()):
    print('{0}  {1:>12,}'.format(k, v))
print('\n--- 按模型(前 8) ---')
for k, v in by_model.most_common(8):
    print('{0:30} {1:>12,}'.format(k, v))
