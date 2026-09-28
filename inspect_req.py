import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import json
txt = open(os.path.join(_ROOT, "logs", "responses_capture.log"), encoding='utf-8').read()
blocks = txt.split('====')
for b in blocks:
    if 'gpt-5.4-mini' in b and 'BODY:' in b:
        body = b.split('BODY:', 1)[1].strip()
        j = json.loads(body)
        print('TOP KEYS:', list(j.keys()))
        print('model =', j.get('model'), '| stream =', j.get('stream'), '| store =', j.get('store'))
        print('tool_choice =', j.get('tool_choice'), '| parallel =', j.get('parallel_tool_calls'))
        print('reasoning =', json.dumps(j.get('reasoning'), ensure_ascii=False))
        inp = j.get('input')
        print('INPUT python type =', type(inp).__name__)
        print('--- INPUT (first 2500 chars) ---')
        print(json.dumps(inp, ensure_ascii=False, indent=1)[:2500])
        print('--- TOOLS ---')
        for t in j.get('tools', []):
            print('type =', t.get('type'), '| name =', t.get('name'), '| keys =', list(t.keys()))
        break
