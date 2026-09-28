#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TokenEngine 哨兵仪表盘：聚合配置 / 实时状态 / 用量日志，渲染图形化 Web UI。
纯标准库。由 router.py 调用：build_metrics() 取数，render_html() 出页面。
"""
import json, os, datetime, html as _html

# 已知免费额度（仅作参考；积分与 token 并非 1:1、无法可靠换算的，amount 置 None，不画百分比条以免假警报）
QUOTA = {
    "sensenova": {"amount": None, "window_h": 5,
                  "label": "官方约6万积分 / 滚动5h；积分与 token 并非 1:1，无法换算成 token 百分比，额度用尽会以 429/失败记录体现"},
}

def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def _parse_usage(path):
    """返回 (records, totals)。"""
    recs = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    recs.append(json.loads(line))
                except Exception:
                    pass
    except FileNotFoundError:
        pass
    return recs

def _parse_fail(path):
    """fail.log 每行是一个 JSON 记录；解析为 dict，无法解析的行包装成 dict 以兼容渲染。"""
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    if isinstance(rec, dict):
                        out.append(rec)
                    else:
                        out.append({"ts": "", "provider": "?", "err": str(rec)})
                except Exception:
                    out.append({"ts": "", "provider": "?", "err": line})
    except FileNotFoundError:
        pass
    return out[-30:]

def _parse_dt(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:
        return None

def build_metrics(base, live, cooldown=None, accounts=None):
    cooldown = cooldown or {}
    accounts = accounts or {}
    cfg = _read_json(os.path.join(base, "config", "providers.json")) or {"providers": []}
    pool = _read_json(os.path.join(base, "logs", "token_pool.json")) or {}
    health = pool.get("providers", {})
    recs = _parse_usage(os.path.join(base, "logs", "usage.log"))
    fails = _parse_fail(os.path.join(base, "logs", "fail.log"))

    now = datetime.datetime.now()
    w5 = now - datetime.timedelta(hours=5)
    w24 = now - datetime.timedelta(hours=24)

    tot = {"in": 0, "out": 0, "total": 0, "req": 0}
    by_prov = {}
    by_day = {}
    last14 = {}
    recent = []

    def _slot(d, name):
        return d.setdefault(name, {"in": 0, "out": 0, "total": 0, "req": 0,
                                   "h5": 0, "d24": 0, "last": None, "models": set()})

    for r in recs:
        i = r.get("in", 0); o = r.get("out", 0); t = r.get("total", 0)
        prov = r.get("provider", "?")
        dt = _parse_dt(r.get("ts", ""))
        tot["in"] += i; tot["out"] += o; tot["total"] += t; tot["req"] += 1
        s = _slot(by_prov, prov)
        s["in"] += i; s["out"] += o; s["total"] += t; s["req"] += 1
        s["models"].add(r.get("model", "?"))
        if dt and (s["last"] is None or dt > s["last"]):
            s["last"] = dt
        if dt and dt >= w5:
            s["h5"] += t
        if dt and dt >= w24:
            s["d24"] += t
        day = (r.get("ts", "") or "")[:10]
        dd = by_day.setdefault(day, {}); ds = _slot(dd, prov)
        ds["total"] += t; ds["req"] += 1
        if dt and dt >= now - datetime.timedelta(days=14):
            last14.setdefault(day, {})[prov] = last14.get(day, {}).get(prov, 0) + t

    recent = recs[-12:][::-1]

    providers = []
    for p in sorted(cfg.get("providers", []), key=lambda x: x.get("priority", 99)):
        name = p["name"]
        s = by_prov.get(name, {"in": 0, "out": 0, "total": 0, "req": 0, "h5": 0,
                               "d24": 0, "last": None, "models": set()})
        lv = live.get(name, {})
        h = health.get(name, {})
        q = QUOTA.get(name)
        last_fail = None
        for fl in reversed(fails):
            if fl.get("provider") == name:
                last_fail = fl; break
        providers.append({
            "name": name,
            "enabled": p.get("enabled", True),
            "priority": p.get("priority", 99),
            "host": (p.get("base_url", "").split("//")[-1].split("/")[0]),
            "proxy": p.get("proxy"),
            "models": p.get("models_out", []),
            "map": p.get("model_map", {}),
            "note": p.get("_note", ""),
            "health_ok": h.get("ok"),
            "health_detail": h.get("detail", ""),
            "stat": s,
            "live": lv,
            "quota": q,
            "last_fail": last_fail,
            "cooldown": cooldown.get(name),
            "accounts": accounts.get(name),
        })

    days = sorted(last14.keys())
    day_series = []
    for d in days:
        row = {"day": d[5:]}
        for prov in {p["name"] for p in providers}:
            row[prov] = last14[d].get(prov, 0)
        day_series.append(row)

    return {
        "now": now,
        "tot": tot,
        "providers": providers,
        "day_series": day_series,
        "recent": recent,
        "fails": fails[::-1][:10],
        "prov_names": [p["name"] for p in providers],
    }

# ---------------------------- 渲染：可交互控制台 ----------------------------
#
# 设计要点：
#   * 纯标准库，不引任何 CDN / 第三方资源（离线可用）
#   * 首屏数据由服务端注入（window.__BOOT__），之后由 JS fetch /api/metrics 局部刷新，
#     不再整页 <meta refresh>，避免操作中被刷掉
#   * 写操作走 POST /api/providers | /api/cooldown | /api/test（回环免鉴权）

_COLORS = ["#58a6ff", "#3fb950", "#d29922", "#bc8cff", "#f85149", "#39c5cf"]
_COLOR = {}


def _color(name, i=None):
    if name not in _COLOR:
        _COLOR[name] = _COLORS[len(_COLOR) % len(_COLORS)]
    return _COLOR[name]


def _esc(x):
    return _html.escape(str(x if x is not None else ""))


def _fmt(n):
    try:
        return format(int(n), ",")
    except Exception:
        return str(n)


def _ago(dt, now):
    if not dt:
        return "—"
    sec = int((now - dt).total_seconds())
    if sec < 60:
        return f"{sec}秒前"
    if sec < 3600:
        return f"{sec // 60}分钟前"
    if sec < 86400:
        return f"{sec // 3600}小时前"
    return f"{sec // 86400}天前"


def _jsonable(m):
    """把 build_metrics 的结果转成可 JSON 序列化的结构（datetime -> ISO 字符串）。"""
    def conv(p):
        s = dict(p["stat"])
        s["models"] = sorted(s.get("models") or [])
        s["last"] = s["last"].isoformat(timespec="seconds") if s.get("last") else None
        out = dict(p)
        out["stat"] = s
        return out

    return {
        "now": m["now"].isoformat(timespec="seconds"),
        "tot": m["tot"],
        "providers": [conv(p) for p in m["providers"]],
        "day_series": m["day_series"],
        "prov_names": m["prov_names"],
        "recent": m["recent"],
        "fails": m["fails"],
    }


_CSS = """
*{box-sizing:border-box}
:root{
  --bg:#0d1117;--bg2:#161b22;--bg3:#1c2330;--line:#30363d;
  --fg:#e6edf3;--fg2:#8b949e;--fg3:#6e7681;
  --blue:#58a6ff;--green:#3fb950;--amber:#d29922;--red:#f85149;--purple:#bc8cff;--teal:#39c5cf;
}
body{margin:0;background:var(--bg);color:var(--fg);font-size:13.5px;line-height:1.6;
  font-family:-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:16px 18px 60px}
a{color:var(--blue);text-decoration:none}
.topbar{display:flex;align-items:center;gap:14px;flex-wrap:wrap;
  padding:12px 0 14px;border-bottom:1px solid var(--line);margin-bottom:18px}
.brand{font-size:17px;font-weight:600;letter-spacing:-.01em}
.brand span{color:var(--fg2);font-weight:400;font-size:13px;margin-left:6px}
.spacer{flex:1}
.st{display:flex;align-items:center;gap:7px;font-size:12.5px;color:var(--fg2)}
.dot{width:8px;height:8px;border-radius:50%;background:var(--fg3);display:inline-block}
.dot.ok{background:var(--green);box-shadow:0 0 0 3px rgba(63,185,80,.15)}
.dot.bad{background:var(--red);box-shadow:0 0 0 3px rgba(248,81,73,.15)}
button{font:inherit;font-size:12.5px;cursor:pointer;border-radius:7px;
  border:1px solid var(--line);background:var(--bg3);color:var(--fg);padding:5px 11px}
button:hover{border-color:var(--fg3);background:#222c3a}
button:disabled{opacity:.5;cursor:default}
button.primary{border-color:#1f6feb;background:#1f6feb;color:#fff}
button.primary:hover{background:#388bfd}
button.danger{border-color:#6e2b2b;color:#ff9492}
button.danger:hover{background:#3a1d1d}
label.chk{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;color:var(--fg2);cursor:pointer}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;margin-bottom:22px}
.stat{background:var(--bg2);border:1px solid var(--line);border-radius:11px;padding:13px 15px}
.stat .v{font-size:21px;font-weight:600;letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.stat .k{font-size:11.5px;color:var(--fg2);margin-top:2px}
.stat .v.b{color:var(--blue)}.stat .v.g{color:var(--green)}
.stat .v.a{color:var(--amber)}.stat .v.p{color:var(--purple)}
h3{font-size:14px;font-weight:600;margin:0 0 12px}
.sect{display:flex;align-items:baseline;gap:10px;margin:0 0 12px}
.sect h3{margin:0}
.hint{font-size:11.5px;color:var(--fg3)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(345px,1fr));gap:11px;margin-bottom:22px}
.card{background:var(--bg2);border:1px solid var(--line);border-radius:12px;padding:13px 15px;transition:border-color .15s}
.card:hover{border-color:var(--fg3)}
.card.off{opacity:.62}
.card-h{display:flex;align-items:center;gap:8px;flex-wrap:wrap;cursor:pointer;user-select:none}
.card-h b{font-size:14px}
.caret{color:var(--fg3);font-size:10px;transition:transform .15s}
.caret.open{transform:rotate(90deg)}
.host{font-size:11.5px;color:var(--fg3);margin:3px 0 9px;word-break:break-all}
.badge{font-size:11px;padding:1.5px 8px;border-radius:20px;white-space:nowrap}
.badge.green{background:rgba(63,185,80,.16);color:#56d364}
.badge.amber{background:rgba(210,153,34,.16);color:#e3b341}
.badge.red{background:rgba(248,81,73,.16);color:#ff7b72}
.badge.gray{background:rgba(139,148,158,.16);color:var(--fg2)}
.badge.blue{background:rgba(88,166,255,.16);color:#79c0ff}
.nums{display:grid;grid-template-columns:repeat(4,1fr);gap:6px;margin:9px 0}
.nums div{background:var(--bg3);border-radius:8px;padding:6px 2px;text-align:center}
.num{font-weight:600;font-size:13px;font-variant-numeric:tabular-nums}
.lbl{color:var(--fg3);font-size:10.5px;display:block;margin-top:1px}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin:8px 0 0}
.chip{font-size:11px;background:rgba(88,166,255,.1);color:#79c0ff;border-radius:6px;padding:1.5px 7px}
.cd{font-size:11.5px;color:#e3b341;background:rgba(210,153,34,.09);border-radius:7px;padding:5px 8px;margin:7px 0}
.fail{font-size:11.5px;color:#ff9492;background:rgba(248,81,73,.08);border-radius:7px;padding:5px 8px;margin:7px 0;word-break:break-all}
.ops{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px;padding-top:10px;border-top:1px solid var(--line)}
.prio{width:52px;font:inherit;font-size:12px;background:var(--bg3);border:1px solid var(--line);
  color:var(--fg);border-radius:7px;padding:4px 7px;text-align:center}
.acc{margin-top:10px;border-top:1px dashed var(--line);padding-top:9px}
.acc table{width:100%;border-collapse:collapse;font-size:11.5px}
.acc th,.acc td{padding:4px 6px;text-align:left;border-bottom:1px solid rgba(48,54,61,.5)}
.acc th{color:var(--fg3);font-weight:500;font-size:10.5px}
.acc td.r{text-align:right;font-variant-numeric:tabular-nums}
.bar{height:7px;border-radius:5px;background:var(--bg3);overflow:hidden;margin:5px 0 2px}
.bar i{display:block;height:100%}
.panel{background:var(--bg2);border:1px solid var(--line);border-radius:12px;padding:15px 17px;margin-bottom:16px}
.tabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.tab{font-size:12px;padding:4px 12px;border-radius:20px;border:1px solid var(--line);
  background:transparent;color:var(--fg2);cursor:pointer}
.tab.active{background:rgba(88,166,255,.14);border-color:rgba(88,166,255,.4);color:#79c0ff}
.chart{width:100%;height:auto;display:block}
.grid-l{stroke:rgba(48,54,61,.7);stroke-width:1}
.axis{fill:var(--fg3);font-size:10px}
.legend{display:flex;gap:13px;flex-wrap:wrap;margin-top:9px;font-size:11.5px;color:var(--fg2)}
.legend i{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}
table.logs{width:100%;border-collapse:collapse;font-size:12px}
table.logs th,table.logs td{padding:6px 8px;text-align:left;border-bottom:1px solid rgba(48,54,61,.5);vertical-align:top}
table.logs th{color:var(--fg3);font-weight:500;font-size:11px;position:sticky;top:0;background:var(--bg2)}
table.logs td.r{text-align:right;font-variant-numeric:tabular-nums}
table.logs td.mono{font-family:Consolas,monospace;font-size:11.5px;color:var(--fg2);word-break:break-all}
.scroll{max-height:340px;overflow:auto}
.muted{color:var(--fg2)}.small{font-size:11.5px}
.empty{color:var(--fg3);padding:14px 0;font-size:12.5px}
#toast{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:50;max-width:360px}
.toast{background:var(--bg3);border:1px solid var(--line);border-left:3px solid var(--blue);
  border-radius:8px;padding:9px 13px;font-size:12.5px;color:var(--fg);box-shadow:0 6px 22px rgba(0,0,0,.5)}
.toast.err{border-left-color:var(--red)}
.toast.ok{border-left-color:var(--green)}
@media(max-width:640px){.nums{grid-template-columns:repeat(2,1fr)}.wrap{padding:12px}}
"""

_JS = """
const C=["#58a6ff","#3fb950","#d29922","#bc8cff","#f85149","#39c5cf"];
const CM={};function color(n){if(!CM[n])CM[n]=C[Object.keys(CM).length%C.length];return CM[n];}
const openSet=new Set();let DATA=null,LOGKIND="fail",timer=null;

function esc(s){return String(s==null?"":s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}
function fmt(n){const v=Number(n);return isFinite(v)?v.toLocaleString("en-US"):String(n);}
function ago(iso){if(!iso)return "\\u2014";const d=(Date.now()-new Date(iso).getTime())/1000;
  if(d<60)return Math.floor(d)+"\\u79d2\\u524d";if(d<3600)return Math.floor(d/60)+"\\u5206\\u949f\\u524d";
  if(d<86400)return Math.floor(d/3600)+"\\u5c0f\\u65f6\\u524d";return Math.floor(d/86400)+"\\u5929\\u524d";}

function toast(msg,kind){const w=document.getElementById("toast");const d=document.createElement("div");
  d.className="toast "+(kind||"");d.textContent=msg;w.appendChild(d);
  setTimeout(()=>{d.style.opacity="0";setTimeout(()=>d.remove(),300);},3600);}

function setStatus(ok,txt){const dot=document.getElementById("dot"),t=document.getElementById("statusText");
  dot.className="dot "+(ok?"ok":"bad");t.textContent=txt||(ok?"\\u5df2\\u8fde\\u63a5":"\\u8fde\\u63a5\\u5931\\u8d25");}

async function act(path,body,label){
  try{
    const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
    const j=await r.json();
    if(r.ok&&j.ok!==false){toast((label||"\\u64cd\\u4f5c")+"\\u6210\\u529f","ok");}
    else{toast((label||"\\u64cd\\u4f5c")+"\\u5931\\u8d25\\uff1a"+(j.error||JSON.stringify(j)),"err");}
    await refresh();return j;
  }catch(e){toast("\\u8bf7\\u6c42\\u5f02\\u5e38\\uff1a"+e.message,"err");}
}

function renderStats(d){
  const t=d.tot,ps=d.providers||[];
  const live=ps.filter(p=>p.enabled).length;
  const cool=ps.filter(p=>p.cooldown).length;
  const okRate=(()=>{let ok=0,all=0;ps.forEach(p=>{ok+=(p.live.ok||0);all+=(p.live.ok||0)+(p.live.fail||0);});
    return all?Math.round(ok/all*100)+"%":"\\u2014";})();
  const items=[
    ["\\u603b\\u8bf7\\u6c42",fmt(t.req),""],
    ["\\u8f93\\u5165 token",fmt(t.in),"b"],
    ["\\u8f93\\u51fa token",fmt(t.out),"p"],
    ["\\u5408\\u8ba1 token",fmt(t.total),"b"],
    ["\\u542f\\u7528\\u6e90",live+"/"+ps.length,"g"],
    ["\\u51b7\\u5374\\u4e2d",String(cool),cool?"a":""],
    ["\\u5b9e\\u65f6\\u6210\\u529f\\u7387",okRate,"g"],
  ];
  document.getElementById("stats").innerHTML=items.map(([k,v,c])=>
    `<div class="stat"><div class="v ${c}">${esc(v)}</div><div class="k">${k}</div></div>`).join("");
}

function statusBadge(p){
  if(!p.enabled)return ['<span class="badge gray">\\u5df2\\u505c\\u7528</span>',"#6e7681"];
  if(p.cooldown)return ['<span class="badge amber">\\u51b7\\u5374\\u4e2d</span>',"#d29922"];
  if(p.health_ok===false)return ['<span class="badge red">\\u54e8\\u5175\\u5224\\u6b7b</span>',"#f85149"];
  const last=p.stat&&p.stat.last;
  if(last&&(Date.now()-new Date(last).getTime())<600000)return ['<span class="badge green">\\u5728\\u7528</span>',"#3fb950"];
  if(last)return ['<span class="badge amber">\\u5f85\\u673a</span>',"#d29922"];
  return ['<span class="badge gray">\\u65e0\\u8bb0\\u5f55</span>',"#6e7681"];
}

function renderProviders(d){
  const ps=d.providers||[];
  document.getElementById("providers").innerHTML=ps.map(p=>{
    const [badge,dot]=statusBadge(p);const s=p.stat||{};const lv=p.live||{};
    const models=(s.models&&s.models.length?s.models:p.models)||[];
    const open=openSet.has(p.name);
    const accs=(p.accounts&&p.accounts.accounts)||[];
    let accHtml="";
    if(open){
      accHtml=`<div class="acc">`+(accs.length?`<table><thead><tr><th>\\u8d26\\u53f7</th><th>Key</th><th>\\u72b6\\u6001</th><th class="r">\\u5e76\\u53d1</th><th class="r">\\u51b7\\u5374</th><th class="r">\\u901f\\u7387</th><th class="r">\\u6210\\u529f\\u7387</th></tr></thead><tbody>`+
        accs.map(a=>`<tr><td>${esc(a.name)}</td><td class="mono">${esc(a.key)}</td><td>${esc(a.status)}</td>
          <td class="r">${a.inflight}/${a.max_concurrency}</td><td class="r">${a.cooldown_remaining||0}s</td>
          <td class="r">${a.rate!=null?a.rate:"-"}</td><td class="r">${a.success_rate!=null?(a.success_rate*100).toFixed(0)+"%":"-"}</td></tr>`).join("")+
        `</tbody></table>`:`<div class="empty">\\u672a\\u914d\\u8d26\\u53f7\\u6c60\\uff08\\u5355 Key \\u6a21\\u5f0f\\uff09</div>`)+`</div>`;
    }
    return `<div class="card ${p.enabled?"":"off"}" id="c_${esc(p.name)}">
      <div class="card-h" onclick="toggleAcc('${esc(p.name)}')">
        <span class="caret ${open?"open":""}">\\u25b6</span>
        <span class="dot" style="background:${dot};width:8px;height:8px;border-radius:50%;display:inline-block"></span>
        <b>${esc(p.name)}</b><span class="muted small">P${p.priority}</span>${badge}
        ${p.proxy?`<span class="badge blue">\\u4ee3\\u7406</span>`:""}
      </div>
      <div class="host">${esc(p.host||"")}</div>
      ${p.cooldown?`<div class="cd">\\u7194\\u65ad\\u51b7\\u5374\\uff1a\\u5269\\u4f59 ${p.cooldown.remaining}s\\uff08${esc(p.cooldown.reason||"")}\\uff09</div>`:""}
      <div class="nums">
        <div><span class="num">${fmt(s.total)}</span><span class="lbl">\\u7d2f\\u8ba1token</span></div>
        <div><span class="num">${fmt(s.req)}</span><span class="lbl">\\u8bf7\\u6c42</span></div>
        <div><span class="num">${fmt(s.d24)}</span><span class="lbl">\\u8fd124h</span></div>
        <div><span class="num">${ago(s.last)}</span><span class="lbl">\\u6700\\u8fd1\\u6210\\u529f</span></div>
      </div>
      <div class="chips">${models.slice(0,8).map(m=>`<span class="chip">${esc(m)}</span>`).join("")}${models.length>8?`<span class="chip">+${models.length-8}</span>`:""}</div>
      <div class="small muted" style="margin-top:7px">\\u5b9e\\u65f6\\u8ba1\\u6570\\uff1a\\u6210\\u529f ${fmt(lv.ok||0)} / \\u5931\\u8d25 ${fmt(lv.fail||0)}</div>
      ${p.last_fail?`<div class="fail">\\u6700\\u8fd1\\u5931\\u8d25 ${esc((p.last_fail.ts||"").slice(11,19))}\\uff1a${esc((p.last_fail.err||"").slice(0,110))}</div>`:""}
      <div class="ops" onclick="event.stopPropagation()">
        <button class="${p.enabled?"danger":"primary"}" onclick="toggleProv('${esc(p.name)}',${p.enabled?"false":"true"})">${p.enabled?"\\u505c\\u7528":"\\u542f\\u7528"}</button>
        <button onclick="clearCd('${esc(p.name)}')">\\u91cd\\u7f6e\\u51b7\\u5374</button>
        <button onclick="testProv('${esc(p.name)}',this)">\\u8fde\\u901a\\u6d4b\\u8bd5</button>
        <span class="small muted" style="align-self:center">\\u4f18\\u5148\\u7ea7</span>
        <input class="prio" type="number" min="1" value="${p.priority}" onchange="setPrio('${esc(p.name)}',this.value)">
      </div>
      ${accHtml}
    </div>`;
  }).join("");
}

function toggleAcc(n){if(openSet.has(n))openSet.delete(n);else openSet.add(n);if(DATA)renderProviders(DATA);}
function toggleProv(n,en){act("/api/providers",{action:en?"enable":"disable",provider:n},en?"\\u542f\\u7528 "+n:"\\u505c\\u7528 "+n);}
function clearCd(n){act("/api/cooldown",{action:"clear",provider:n},"\\u91cd\\u7f6e\\u51b7\\u5374 "+n);}
function setPrio(n,v){const p=parseInt(v,10);if(!p||p<1)return;act("/api/providers",{action:"set_priority",provider:n,priority:p},"\\u8c03\\u6574\\u4f18\\u5148\\u7ea7 "+n);}
async function testProv(n,btn){
  const old=btn.textContent;btn.disabled=true;btn.textContent="\\u6d4b\\u8bd5\\u4e2d\\u2026";
  try{
    const r=await fetch("/api/test",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({provider:n})});
    const j=await r.json();
    const rs=j.results||[];
    const line=rs.map(x=>x.ok?`${x.model} \\u2713 ${x.latency_ms}ms`:`${x.model} \\u2717 ${(x.error||"").slice(0,60)}`).join(" | ");
    toast(line||"\\u65e0\\u7ed3\\u679c",j.ok?"ok":"err");
    await refresh();
  }catch(e){toast("\\u6d4b\\u8bd5\\u5f02\\u5e38\\uff1a"+e.message,"err");}
  finally{btn.disabled=false;btn.textContent=old;}
}

function renderChart(d){
  const series=d.day_series||[],names=d.prov_names||[];
  const el=document.getElementById("chart");
  if(!series.length){el.innerHTML='<div class="empty">\\u8fd114\\u5929\\u6682\\u65e0\\u6570\\u636e</div>';return;}
  const W=860,H=210,pl=52,pb=26,pt=10,pw=W-pl-12,ph=H-pb-pt;
  const maxv=Math.max(1,...series.map(r=>names.reduce((a,n)=>a+(r[n]||0),0)));
  const n=series.length,bw=pw/n*0.6,gap=pw/n;let o=[`<svg viewBox="0 0 ${W} ${H}" class="chart" role="img">`];
  for(let g=0;g<4;g++){const y=pt+ph-ph*g/3,v=Math.round(maxv*g/3);
    o.push(`<line x1="${pl}" y1="${y.toFixed(1)}" x2="${W-12}" y2="${y.toFixed(1)}" class="grid-l"/>`);
    o.push(`<text x="${pl-6}" y="${(y+3).toFixed(1)}" text-anchor="end" class="axis">${fmt(v)}</text>`);}
  series.forEach((row,i)=>{const x=pl+i*gap+(gap-bw)/2;let yc=pt+ph;
    names.forEach(nm=>{const v=row[nm]||0;if(v<=0)return;const h=ph*v/maxv;yc-=h;
      o.push(`<rect x="${x.toFixed(1)}" y="${yc.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" fill="${color(nm)}"><title>${esc(nm)} ${fmt(v)}</title></rect>`);});
    o.push(`<text x="${(x+bw/2).toFixed(1)}" y="${H-pb+15}" text-anchor="middle" class="axis">${esc(row.day)}</text>`);});
  o.push("</svg>");
  o.push('<div class="legend">'+names.map(nm=>`<span><i style="background:${color(nm)}"></i>${esc(nm)}</span>`).join("")+"</div>");
  el.innerHTML=o.join("");
}

async function loadLogs(kind){
  LOGKIND=kind||LOGKIND;
  document.querySelectorAll(".tab").forEach(t=>t.classList.toggle("active",t.dataset.kind===LOGKIND));
  const el=document.getElementById("logs");el.innerHTML='<div class="empty">\\u52a0\\u8f7d\\u4e2d\\u2026</div>';
  try{
    const r=await fetch("/api/logs",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({kind:LOGKIND,lines:60})});
    const j=await r.json();
    const rows=(j.lines||[]).reverse();
    if(!rows.length){el.innerHTML='<div class="empty">\\u65e0\\u8bb0\\u5f55</div>';return;}
    if(LOGKIND==="usage"){
      el.innerHTML='<div class="scroll"><table class="logs"><thead><tr><th>\\u65f6\\u95f4</th><th>\\u6e90</th><th>\\u6a21\\u578b</th><th class="r">\\u5165</th><th class="r">\\u51fa</th><th class="r">\\u5408\\u8ba1</th></tr></thead><tbody>'+
        rows.map(x=>`<tr><td class="mono">${esc((x.ts||"").slice(5,19).replace("T"," "))}</td><td>${esc(x.provider||"?")}</td>
          <td class="mono">${esc(x.model||"")}</td><td class="r">${fmt(x.in)}</td><td class="r">${fmt(x.out)}</td><td class="r">${fmt(x.total)}</td></tr>`).join("")+
        "</tbody></table></div>";
    }else{
      el.innerHTML='<div class="scroll"><table class="logs"><thead><tr><th>\\u65f6\\u95f4</th><th>\\u6e90</th><th>\\u8be6\\u60c5</th></tr></thead><tbody>'+
        rows.map(x=>`<tr><td class="mono">${esc((x.ts||"").slice(5,19).replace("T"," "))}</td><td>${esc(x.provider||"")}</td>
          <td class="mono">${esc((x.err||x.raw||"").slice(0,200))}</td></tr>`).join("")+
        "</tbody></table></div>";
    }
  }catch(e){el.innerHTML=`<div class="empty">\\u52a0\\u8f7d\\u5931\\u8d25\\uff1a${esc(e.message)}</div>`;}
}

function render(d){
  document.getElementById("stamp").textContent=d.now.replace("T"," ");
  renderStats(d);renderProviders(d);renderChart(d);
}

async function refresh(){
  try{
    const r=await fetch("/api/metrics",{cache:"no-store"});
    if(!r.ok)throw new Error("HTTP "+r.status);
    DATA=await r.json();render(DATA);setStatus(true);
  }catch(e){setStatus(false,"\\u8fde\\u63a5\\u5931\\u8d25");}
}

function setAuto(on){
  if(timer){clearInterval(timer);timer=null;}
  if(on)timer=setInterval(refresh,5000);
}

document.addEventListener("DOMContentLoaded",()=>{
  document.querySelectorAll(".tab").forEach(t=>t.onclick=()=>loadLogs(t.dataset.kind));
  document.getElementById("autoRefresh").onchange=e=>setAuto(e.target.checked);
  if(window.__BOOT__){DATA=window.__BOOT__;render(DATA);}
  refresh();loadLogs("fail");setAuto(true);
});
"""

_HTML = """<!doctype html><html lang="zh"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>TokenEngine 控制台</title>
<style>__CSS__</style></head><body>
<div class="wrap">
  <div class="topbar">
    <div class="brand">TokenEngine<span>控制台</span></div>
    <div class="st"><span class="dot" id="dot"></span><span id="statusText">连接中…</span></div>
    <div class="spacer"></div>
    <label class="chk"><input type="checkbox" id="autoRefresh" checked> 自动刷新（5s）</label>
    <button onclick="refresh()">立即刷新</button>
    <span class="muted small" id="stamp"></span>
  </div>
  <div class="stats" id="stats"></div>
  <div class="sect"><h3>源状态</h3><span class="hint">点卡片展开账号池 · 操作即时生效并写入配置</span></div>
  <div class="grid" id="providers"></div>
  <div class="panel"><h3>近 14 天 token 消耗（按源）</h3><div id="chart"></div></div>
  <div class="panel">
    <div class="tabs">
      <button class="tab active" data-kind="fail">失败记录</button>
      <button class="tab" data-kind="usage">用量明细</button>
      <button class="tab" data-kind="supervisor">守护日志</button>
    </div>
    <div id="logs"></div>
  </div>
</div>
<div id="toast"></div>
<script>window.__BOOT__=__DATA__;</script>
<script>__JS__</script>
</body></html>"""


def render_html(m):
    """出页面：注入首屏数据 + 内联 CSS/JS，纯离线可用。"""
    payload = json.dumps(_jsonable(m), ensure_ascii=False).replace("</", "<\\/")
    return (_HTML.replace("__CSS__", _CSS)
                  .replace("__JS__", _JS)
                  .replace("__DATA__", payload))
