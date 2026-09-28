# -*- coding: utf-8 -*-
import os
_ROOT = os.path.dirname(os.path.abspath(__file__))
import json

p = os.path.join(_ROOT, "logs", "last_responses_req.json")
raw = open(p, "rb").read()
print("size", len(raw))
j = json.loads(raw.decode("utf-8"))
print("model:", j.get("model"), " stream:", j.get("stream"))
print("instructions:", (j.get("instructions") or "(none)")[:600])
print("--- input ---")
for it in j.get("input", []):
    t = it.get("type"); role = it.get("role"); c = it.get("content")
    if isinstance(c, str):
        print("[%s/%s] text: %s" % (t, role, c[:200]))
    elif isinstance(c, list):
        for part in c:
            pt = part.get("type", "")
            if "image" in pt:
                u = part.get("image_url") or part.get("url") or ""
                if isinstance(u, dict):
                    u = u.get("url", "")
                print("[%s/%s] IMAGE type=%s url_len=%d prefix=%s" % (t, role, pt, len(u), u[:36]))
            else:
                print("[%s/%s] %s: %s" % (t, role, pt, str(part.get("text"))[:200]))
    else:
        print("[%s/%s] keys=%s" % (t, role, list(it.keys())))
print("--- tools ---")
for tool in j.get("tools", []):
    print("  ", tool.get("type"), tool.get("name"), str(tool.get("description"))[:90])
