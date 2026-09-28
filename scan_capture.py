import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import json
txt = open(os.path.join(_ROOT, "logs", "responses_capture.log"), encoding='utf-8').read()
blocks = txt.split('====')
ok = 0
found = False
for b in blocks:
    if 'BODY:' not in b:
        continue
    body = b.split('BODY:', 1)[1].strip()
    try:
        j = json.loads(body)
    except Exception:
        continue
    ok += 1
    if 'instructions' in j and not found:
        found = True
        print('PARSED OK. top keys =', list(j.keys()))
        print('model =', j.get('model'), '| stream =', j.get('stream'), '| store =', j.get('store'))
        print('reasoning =', json.dumps(j.get('reasoning'), ensure_ascii=False))
        inp = j.get('input')
        print('INPUT type =', type(inp).__name__)
        print('--- INPUT ---')
        print(json.dumps(inp, ensure_ascii=False, indent=1)[:2200])
        print('--- TOOLS ---')
        for t in j.get('tools', []):
            print('type =', t.get('type'), '| name =', t.get('name'), '| keys =', list(t.keys()))
print('parseable blocks =', ok)
