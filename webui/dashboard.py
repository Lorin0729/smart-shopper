"""本地只读 Web 面板：python main.py serve。

零第三方依赖（标准库 http.server），提供：
- GET /            交互式面板（搜索、排序、价格曲线）
- GET /api/summary 所有商品的统计数据 JSON
- GET /api/health  健康检查

面板只读，不会触发抓取，避免误操作。
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from core import sparkline
from core.storage import Store
from predictor import season
from predictor.forecast import PriceHistory, predict_drop, recommend

MAX_POINTS = 140


def _downsample(points, limit: int = MAX_POINTS):
    """等距抽样，避免历史太长把前端拖慢。"""
    if len(points) <= limit:
        return points
    step = len(points) / limit
    return [points[int(i * step)] for i in range(limit - 1)] + [points[-1]]


def product_payload(store: Store, p, today: date | None = None) -> dict:
    today = today or date.today()
    did = p.display_id()
    points = store.get_prices(did)
    hist = PriceHistory(points)
    stats = hist.stats()
    prof = season.profile_of(p.category)
    windows = season.best_buy_windows(p.category, p.launch(), today)
    prediction = predict_drop(hist, today)
    inv = store.last_inventory(did)
    variants = store.latest_variants(did)
    rec = recommend(hist, inv, prediction, windows, today)

    sampled = _downsample(points)
    payload = {
        "id": did,
        "name": p.name or did,
        "platform": p.platform,
        "url": p.url,
        "category": prof.name,
        "target_price": p.target_price,
        "launch_date": p.launch_date,
        "watched_variant": p.watched_variant,
        "points": [[int(pt.ts * 1000), pt.price] for pt in sampled],
        "spark": sparkline.sparkline([pt.price for pt in points]),
        "current": stats.current if stats else None,
        "low": stats.low if stats else None,
        "high": stats.high if stats else None,
        "avg": stats.avg if stats else None,
        "percentile": stats.percentile if stats else None,
        "samples": stats.count if stats else 0,
        "gap_to_low": (round(stats.current - stats.low, 2) if stats else None),
        "inventory": (None if not inv else {
            "in_stock": inv.in_stock, "state": inv.state, "quantity": inv.quantity,
        }),
        "variants": [{"variant": v.variant, "in_stock": v.in_stock, "state": v.label}
                     for v in variants],
        "action": rec.action,
        "score": rec.score,
        "reasons": rec.reasons,
        "expected_low": rec.expected_low,
        "prediction": {
            "prob": prediction.prob_30d,
            "start": prediction.likely_start.isoformat() if prediction.likely_start else None,
            "end": prediction.likely_end.isoformat() if prediction.likely_end else None,
            "reason": prediction.reason,
        },
        "windows": [{
            "tag": w.tag, "start": w.start.isoformat(), "end": w.end.isoformat(),
            "discount": round(w.expected_discount * 10, 1),
            "reason": w.reason, "active": w.overlaps_today(today),
        } for w in windows[:4]],
    }
    return payload


def build_summary(store: Store, today: date | None = None) -> dict:
    today = today or date.today()
    products = [product_payload(store, p, today) for p in store.list_products()]
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "today": today.isoformat(),
        "count": len(products),
        "products": products,
        "db": store.summary(),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    db_path: str
    server_version = "SmartShopper/0.4"

    def log_message(self, fmt, *args):  # 精简日志
        print(f"[web] {self.address_string()} {fmt % args}")

    def _with_store(self, fn):
        """每个请求用独立 SQLite 连接：连接不能跨线程共享。"""
        st = Store(self.db_path)
        try:
            self._send_json(fn(st))
        finally:
            st.close()

    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send_html(INDEX_HTML)
        elif path == "/api/summary":
            self._with_store(lambda st: build_summary(st))
        elif path == "/api/health":
            self._send_json({"ok": True, "ts": int(time.time())})
        elif path.startswith("/api/product/"):
            key = path.rsplit("/", 1)[-1]

            def _one(st):
                prod = st.get_product(key)
                return product_payload(st, prod) if prod else None

            def _check(st):
                data = _one(st)
                if data is None:
                    self._send_json({"error": "not found"}, status=404)
                else:
                    self._send_json(data)

            st = Store(self.db_path)
            try:
                _check(st)
            finally:
                st.close()
        else:
            self._send_json({"error": "not found"}, status=404)

    def _send_json(self, obj, status: int = 200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(store: Store, host: str = "127.0.0.1", port: int = 8777) -> None:
    handler = type("Handler", (DashboardHandler,), {"db_path": str(store.path)})
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"Smart Shopper 面板已启动：http://{host}:{port}  （Ctrl+C 退出）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Smart Shopper 面板</title>
<style>
:root{--bg:#f2f4f8;--card:#fff;--ink:#101828;--muted:#8b93a7;--accent:#2f6df6;
--good:#12a150;--warn:#f5a524;--bad:#e5484d;--line:#eef0f4}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC',
'Microsoft YaHei',sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 18px 60px}
h1{font-size:22px;margin:0 0 6px;letter-spacing:-.02em}
.sub{font-size:12px;color:var(--muted);margin-bottom:18px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}
input,select{padding:8px 12px;border:1px solid #dfe3ea;border-radius:10px;
font-size:13px;background:#fff;color:var(--ink);outline:none}
input{flex:1;min-width:180px}
input:focus,select:focus{border-color:var(--accent)}
.card{background:var(--card);border-radius:16px;padding:20px 22px;margin-bottom:16px;
box-shadow:0 1px 2px rgba(16,24,40,.05),0 10px 26px -16px rgba(16,24,40,.18)}
.row{display:flex;justify-content:space-between;gap:14px;flex-wrap:wrap;align-items:flex-start}
.name{font-size:16px;font-weight:700}
.meta{font-size:12px;color:var(--muted);margin-top:3px}
.badge{display:inline-block;color:#fff;border-radius:999px;padding:3px 11px;
font-size:12px;font-weight:600}
.stats{display:flex;gap:18px;flex-wrap:wrap;margin:12px 0 4px}
.stat .k{font-size:11px;color:var(--muted)}
.stat .v{font-size:15px;font-weight:600}
.chip{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12px;margin:2px 4px 2px 0}
.ok{background:#e7f6ed;color:#0f7b46}
.no{background:#fdeaea;color:var(--bad)}
.reason{margin:6px 0 0;padding-left:18px;font-size:13px;line-height:1.7;color:#3f4759}
.win{font-size:12px;color:#5b6478;margin-top:8px;line-height:1.8}
a{color:var(--accent);text-decoration:none;word-break:break-all}
.empty{text-align:center;color:var(--muted);padding:60px 0}
.foot{text-align:center;font-size:11px;color:#a4abbc;margin-top:26px}
</style></head>
<body><div class="wrap">
<div style="display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap">
<h1>Smart Shopper 面板</h1>
<span style="font-size:12px;color:var(--muted)" id="gen">加载中…</span></div>
<div class="sub" id="db"></div>
<div class="toolbar">
<input id="q" placeholder="搜索商品名 / 类目 / 平台…" autocomplete="off">
<select id="sort">
<option value="score">按决策分（高→低）</option>
<option value="gap">按距历史低价的差距（小→大）</option>
<option value="price">按当前价（低→高）</option>
<option value="prob">按降价概率（高→低）</option>
</select></div>
<div id="list"></div>
<div class="foot">只读面板 · 数据来自本地 SQLite · 抓取请用 <code>python main.py check</code></div>
</div>
<script>
const A={立即购买:'var(--good)',尽快入手:'var(--good)',建议等待:'var(--warn)',关注补货:'var(--bad)'};
let DATA=[];
const fmt=n=>n==null?'-':'¥'+Number(n).toLocaleString('zh-CN',{maximumFractionDigits:1});
function chart(pts,target,w=520,h=150){
  if(!pts||pts.length<2)return '<div style="height:150px;background:#f6f7f9;border-radius:10px;'+
    'display:flex;align-items:center;justify-content:center;color:#8b93a7;font-size:13px">暂无价格数据</div>';
  const t0=pts[0][0],t1=pts[pts.length-1][0]||t0+1,ps=pts.map(p=>p[1]);
  const lo=Math.min(...ps,target||Math.min(...ps)),hi=Math.max(...ps,target||Math.max(...ps));
  const pad=(hi-lo)*0.12||1,l=lo-pad,hh=hi+pad;
  const X=t=>((t-t0)/(t1-t0||1))*(w-64)+52, Y=v=>(hh-v)/(hh-l)*(h-26)+10;
  const line=pts.map(p=>X(p[0]).toFixed(1)+','+Y(p[1]).toFixed(1)).join(' ');
  const area='52,'+(h-16)+' '+line+' '+(w-12)+','+(h-16);
  const lowI=ps.indexOf(Math.min(...ps));
  let grid='';
  for(let i=0;i<4;i++){const y=10+(h-26)*i/3;const v=hh-(hh-l)*i/3;
    grid+='<line x1="52" y1="'+y+'" x2="'+(w-12)+'" y2="'+y+'" stroke="#e8ebf1"/>'+
    '<text x="46" y="'+(y+4)+'" text-anchor="end" font-size="10" fill="#8b93a7">¥'+Math.round(v)+'</text>';}
  const tline=target?('<line x1="52" y1="'+Y(target).toFixed(1)+'" x2="'+(w-12)+'" y2="'+
    Y(target).toFixed(1)+'" stroke="var(--good)" stroke-dasharray="6 5" stroke-width="1.5"/>'+
    '<text x="'+(w-14)+'" y="'+(Y(target).toFixed(1)-5)+'" text-anchor="end" font-size="10" '+
    'fill="var(--good)">目标 '+fmt(target)+'</text>'):'';
  return '<svg viewBox="0 0 '+w+' '+h+'" style="width:100%;max-width:'+w+'px;display:block">'+
    '<defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1">'+
    '<stop offset="0%" stop-color="#2f6df6" stop-opacity=".22"/>'+
    '<stop offset="100%" stop-color="#2f6df6" stop-opacity=".02"/></linearGradient></defs>'+
    grid+'<polygon points="'+area+'" fill="url(#g)"/>'+
    '<polyline points="'+line+'" fill="none" stroke="var(--accent)" stroke-width="2.2" '+
    'stroke-linejoin="round" stroke-linecap="round"/>'+tline+
    '<circle cx="'+X(pts[lowI][0]).toFixed(1)+'" cy="'+Y(ps[lowI]).toFixed(1)+
    '" r="3.5" fill="var(--bad)"/></svg>';
}
function card(p){
  const badge='<span class="badge" style="background:'+(A[p.action]||'#6b7280')+'">'+p.action+'</span>';
  const stats=[['当前价',fmt(p.current)],['历史最低',fmt(p.low)],['均价',fmt(p.avg)],
    ['分位',p.percentile==null?'-':(p.percentile*100).toFixed(0)+'%'],
    ['距最低',p.gap_to_low==null?'-':fmt(p.gap_to_low)]
  ].map(([k,v])=>'<div class="stat"><div class="k">'+k+'</div><div class="v">'+v+'</div></div>').join('');
  const inv=p.inventory?('<div class="meta">库存：<b>'+(p.inventory.state||(p.inventory.in_stock?'有货':'无货'))+
    (p.inventory.quantity!=null?'（剩 '+p.inventory.quantity+' 件）':'')+'</b></div>'):'';
  const vars=p.variants.length?('<div class="meta">尺码：'+p.variants.map(v=>
    '<span class="chip '+(v.in_stock?'ok':'no')+'">'+v.variant+'</span>').join('')+'</div>'):'';
  const target=p.target_price?('<div class="meta">目标价 <b style="color:var(--good)">'+
    fmt(p.target_price)+'</b></div>'):'';
  const pred=p.prediction?('<div class="win">30 天降价概率 <b>'+(p.prediction.prob*100).toFixed(0)+
    '%</b> · 窗口 '+(p.prediction.start||'-')+' ~ '+(p.prediction.end||'-')+'</div>'):'';
  const wins=p.windows.map(w=>'<div class="win">'+(w.active?'<b style="color:var(--good)">● 当前窗口</b> ':'')+
    w.tag+'：'+w.start+' ~ '+w.end+' · '+w.discount+' 折</div>').join('');
  const reasons=p.reasons.length?'<ul class="reason">'+p.reasons.map(r=>
    '<li>'+r+'</li>').join('')+'</ul>':'';
  return '<section class="card"><div class="row"><div>'+
    '<div class="name">'+p.name+'</div>'+
    '<div class="meta">'+p.id+' · '+p.category+(p.watched_variant?' · 关注 '+p.watched_variant:'')+'</div>'+
    target+inv+vars+'</div><div style="text-align:right">'+badge+
    '<div class="meta">决策分 '+p.score+'/100</div></div></div>'+
    '<div style="margin-top:14px">'+chart(p.points,p.target_price)+'</div>'+
    '<div class="stats">'+stats+'</div>'+pred+wins+reasons+
    '<div class="meta" style="margin-top:10px">来源：<a href="'+p.url+'" target="_blank" rel="noreferrer">'+
    p.url+'</a></div></section>';
}
function render(){
  const q=(document.getElementById('q').value||'').trim().toLowerCase();
  const mode=document.getElementById('sort').value;
  let list=DATA.filter(p=>!q||(p.name+p.id+p.category+p.platform).toLowerCase().includes(q));
  const key={score:p=>-(p.score||0),gap:p=>(p.gap_to_low==null?1e9:p.gap_to_low),
    price:p=>(p.current==null?1e9:p.current),prob:p=>-(p.prediction?p.prediction.prob:0)}[mode];
  list.sort((a,b)=>key(a)-key(b));
  document.getElementById('list').innerHTML=list.length?list.map(card).join(''):
    '<div class="empty">没有匹配的商品</div>';
}
async function load(){
  try{
    const r=await fetch('/api/summary',{cache:'no-store'});
    const d=await r.json();DATA=d.products||[];
    document.getElementById('gen').textContent='更新于 '+d.generated_at;
    document.getElementById('db').textContent='共 '+d.count+' 件商品 · '+
      d.db.price_points+' 条价格 · '+d.db.inventory_points+' 条库存 · '+
      d.db.variant_points+' 条尺码采样';
    render();
  }catch(e){
    document.getElementById('list').innerHTML='<div class="empty">加载失败：'+e+'</div>';
  }
}
document.getElementById('q').addEventListener('input',render);
document.getElementById('sort').addEventListener('change',render);
load();
</script></body></html>"""
