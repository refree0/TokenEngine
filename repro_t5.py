# -*- coding: utf-8 -*-
import json, urllib.request
model = "sensenova:deepseek-v4-flash"
TOOL = {"type": "function", "name": "get_stock_price", "description": "查询A股最新价格",
        "parameters": {"type": "object", "properties": {"symbol": {"type": "string"}},
                       "required": ["symbol"]}}
inp = [
    {"type": "message", "role": "user",
     "content": [{"type": "input_text", "text": "查贵州茅台（600519）股价。"}]},
    {"type": "function_call", "call_id": "c1", "name": "get_stock_price",
     "arguments": '{"symbol": "600519"}'},
    {"type": "function_call_output", "call_id": "c1",
     "output": '{"price": 1499.5, "currency": "CNY"}'},
    {"type": "message", "role": "user",
     "content": [{"type": "input_text", "text": "它比1500元贵还是便宜？差多少？"}]},
]
body = {"model": model, "input": inp, "tools": [TOOL],
        "max_output_tokens": 1024, "stream": True}
rq = urllib.request.Request("http://127.0.0.1:8317/v1/responses",
                             data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with op.open(rq, timeout=90) as r:
        print("HTTP OK")
        print(r.read().decode()[:2500])
except Exception as e:
    import traceback
    traceback.print_exc()
