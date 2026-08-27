# -*- coding: utf-8 -*-
"""
AI 模拟投资引擎（被动执行 30% 回撤容忍计划）
- 实时价：证交所官方盘 (mis.twse.com.tw, 股票) + Yahoo Finance (*.TW, ETF/兜底)
- 模式：
    (无参数)        首次建仓；之后仅更新净值（不自动交易）
    --update        仅刷新实时报价与净值（不交易）
    --propose       计算双週調倉提案 -> rebalance_proposal.json（不执行）
    --execute       执行已保存的調倉提案
- 注意：浏览器因 CORS 限制无法直连 TWSE/Yahoo，实时数据均由本引擎（后台）抓取。
- 状态：portfolio_sim.json；提案：rebalance_proposal.json；仪表板：portfolio_dashboard.html
"""
import json, os, math, ssl, sys, urllib.request, urllib.parse
from datetime import datetime

HERE = os.path.dirname(__file__)
CAPITAL = 100000
FEE_RATE = 0.001425
FEE_MIN = 20
STATE_FILE = os.path.join(HERE, "portfolio_sim.json")
PROP_FILE = os.path.join(HERE, "rebalance_proposal.json")

# 30% 回撤容忍组合（定案）
PORT = {
    "0050":  (0.18, "元大台湾50",       "twse"),
    "2330":  (0.15, "台积电",           "twse"),
    "2454":  (0.08, "联发科",           "twse"),
    "2357":  (0.08, "华硕",             "twse"),
    "00919": (0.13, "群益台湾精选高息", "yahoo"),   # TPEx ETF
    "2412":  (0.08, "中华电信",         "twse"),
    "2884":  (0.05, "玉山金",           "twse"),
}
TAX = {"00919": 0.001}.get            # ETF 0.1%，其余股票 0.3%
NAME = {c: n for c, (_, n, _) in PORT.items()}

def now_iso(): return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
def fmtPct(x): return f"{x*100:.1f}%"
def fmtNum(x): return f"{x:,.0f}"

# ---------- 实时报价抓取（返回 dict） ----------
def get_quote_twse(code):
    url = f"https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_{code}.tw"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0","Referer":"https://mis.twse.com.tw/"})
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(req, timeout=30, context=ctx) as r:
        j = json.loads(r.read().decode("utf-8"))
        m = (j.get("msgArray") or [None])[0]
        if not m: return None
        z, y, h, l, t = m.get("z"), m.get("y"), m.get("h"), m.get("l"), m.get("t")
        price = float(z) if (z and z != "-") else None
        if price is None:
            try:
                b = float(m["b"].split("_")[0]); a = float(m["a"].split("_")[0])
                if b and a: price = (b+a)/2
            except: pass
        if price is None: return None
        prev = float(y) if (y and y != "-") else None
        hi = float(h) if (h and h != "-") else None
        lo = float(l) if (l and l != "-") else None
        return {"price": price, "prev_close": prev, "high": hi, "low": lo,
                "source": "TWSE即時", "ts": t}

def get_quote_yahoo(code):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{code}.TW?interval=1m&range=1d"
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        m = json.loads(r.read().decode("utf-8"))["chart"]["result"][0]["meta"]
        p = m.get("regularMarketPrice")
        if not p: return None
        return {"price": float(p), "prev_close": m.get("previousClose"),
                "high": m.get("regularMarketDayHigh"), "low": m.get("regularMarketDayLow"),
                "source": "Yahoo", "ts": m.get("regularMarketTime")}

def load_finmind_fallback():
    try:
        with open(os.path.join(HERE, "finmind_result.json"), encoding="utf-8") as f:
            return json.load(f)["result"]
    except: return {}

def fetch_quotes(state):
    fm = load_finmind_fallback()
    prices, src, quotes = {}, {}, {}
    for code, (_, _, kind) in PORT.items():
        q = None
        try:
            if kind == "twse":
                q = get_quote_twse(code)
                if q is None: q = get_quote_yahoo(code)
            else:
                q = get_quote_yahoo(code)
        except Exception:
            q = None
        if q is None and code in state.get("last_price", {}):
            q = {"price": state["last_price"][code], "prev_close": None,
                 "high": None, "low": None, "source": "上次價(回退)", "ts": None}
        if q is None and code in fm:
            q = {"price": fm[code]["price"], "prev_close": None,
                 "high": None, "low": None, "source": "FinMind收盤(回退)", "ts": None}
        if q is not None and q.get("price") is not None:
            prices[code] = round(q["price"], 3); src[code] = q.get("source"); quotes[code] = q
            state.setdefault("last_price", {})[code] = round(q["price"], 3)
    return prices, src, quotes

# ---------- 产业新闻抓取（FinMind TaiwanStockNews，免 token，支持 CORS） ----------
def fetch_news(codes, days_back=5):
    out = {}
    today = datetime.now()
    for code in codes:
        got = []
        for d in range(days_back + 1):
            day = (today - __import__("datetime").timedelta(days=d)).strftime("%Y-%m-%d")
            params = {"dataset": "TaiwanStockNews", "data_id": code, "start_date": day}
            url = "https://api.finmindtrade.com/api/v4/data?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    rows = json.loads(r.read().decode("utf-8")).get("data") or []
                    if rows:
                        for x in rows[:8]:
                            got.append({"date": x.get("date"),
                                         "title": x.get("title") or x.get("news_title") or x.get("description") or "",
                                         "source": x.get("source"), "link": x.get("link")})
                        break
            except Exception:
                continue
        out[code] = got
    return out

# ---------- 状态 ----------
def new_state():
    return {"capital": CAPITAL, "cash": CAPITAL, "holdings": {}, "trades": [],
            "snapshots": [], "last_price": {}, "target": {c: w for c, (w, *_ ) in PORT.items()},
            "created": now_iso(), "last_run": None}

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return new_state()

def save_state(s):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, ensure_ascii=False, indent=2)

def add_trade(s, code, side, shares, price, fee, note, tax=0):
    s["trades"].append({"ts": now_iso(), "code": code, "name": NAME[code],
                        "side": side, "shares": shares, "price": price,
                        "fee": fee, "tax": tax, "amount": shares*price, "note": note})

def total_value(s, prices):
    return s["cash"] + sum(h["shares"]*prices.get(c, h.get("avg_cost",0)) for c, h in s["holdings"].items())

# ---------- 建仓 / 调仓 ----------
def establish(s, prices):
    for code, (w, _, _) in PORT.items():
        price = prices[code]; target_amt = w*CAPITAL
        shares = max(1, min(999, int(target_amt/price)))
        gross = shares*price; fee = max(FEE_MIN, round(gross*FEE_RATE))
        s["cash"] -= (gross+fee)
        h = s["holdings"].setdefault(code, {"shares":0,"avg_cost":0.0})
        tot = h["shares"]+shares
        h["avg_cost"] = (h["shares"]*h["avg_cost"]+gross)/tot; h["shares"]=tot
        add_trade(s, code, "buy", shares, price, fee, "初始建仓·被動執行30%計畫")
    return [t for t in s["trades"] if t["note"].startswith("初始")]

def build_proposal(s, prices):
    total = total_value(s, prices)
    orders = []
    for code, (w, _, _) in PORT.items():
        h = s["holdings"].get(code)
        cur = h["shares"] if h else 0
        price = prices[code]
        target_shares = min(999, max(0, int(w*total/price)))
        qty = target_shares - cur
        if qty == 0: continue
        side = "buy" if qty > 0 else "sell"
        orders.append({"code": code, "name": NAME[code], "side": side,
                       "shares": abs(qty), "price": price,
                       "est_amount": abs(qty)*price,
                       "reason": f"權重 {cur*price/total*100:.1f}% → 目標 {w*100:.0f}%"})
    prop = {"asof": now_iso(), "total": round(total,2), "orders": orders,
            "cash_before": round(s["cash"],2), "executed": False}
    with open(PROP_FILE, "w", encoding="utf-8") as f:
        json.dump(prop, f, ensure_ascii=False, indent=2)
    return orders

def execute_proposal(s, prices):
    if not os.path.exists(PROP_FILE): return []
    with open(PROP_FILE, encoding="utf-8") as f:
        prop = json.load(f)
    made = []
    for o in prop.get("orders", []):
        code = o["code"]; price = prices.get(code, o["price"]); qty = o["shares"]
        if qty <= 0: continue
        h = s["holdings"].get(code) or {"shares":0,"avg_cost":0.0}
        if o["side"] == "buy":
            cost = qty*price; fee = max(FEE_MIN, round(cost*FEE_RATE))
            if cost+fee > s["cash"]: continue
            s["cash"] -= (cost+fee)
            tot = h["shares"]+qty
            h["avg_cost"] = (h["shares"]*h["avg_cost"]+cost)/tot; h["shares"]=tot
            s["holdings"][code] = h
            add_trade(s, code, "buy", qty, price, fee, "雙週調倉·增持"); made.append(code)
        else:
            if qty > h["shares"]: qty = h["shares"]
            if qty <= 0: continue
            proceeds = qty*price; fee = max(FEE_MIN, round(proceeds*FEE_RATE))
            tax = round(proceeds*TAX(code))
            s["cash"] += (proceeds-fee-tax)
            h["shares"] -= qty
            if h["shares"] == 0: s["holdings"].pop(code, None)
            add_trade(s, code, "sell", qty, price, fee, "雙週調倉·減碼", tax=tax); made.append(code)
    prop["executed"] = True; prop["executed_at"] = now_iso()
    with open(PROP_FILE, "w", encoding="utf-8") as f:
        json.dump(prop, f, ensure_ascii=False, indent=2)
    return made

# ---------- 主流程 ----------
MODE = sys.argv[1] if len(sys.argv) > 1 else None
s = load_state()
prices, src, quotes = fetch_quotes(s)
news = fetch_news(list(PORT.keys()))

if MODE == "--propose":
    orders = build_proposal(s, prices)
    action = "雙週調倉提案(未執行)"
    trades_this = []
elif MODE == "--execute":
    trades_this = execute_proposal(s, prices)
    action = "雙週調倉執行" if trades_this else "提案已執行/無動作"
    orders = []
elif MODE == "--update":
    action = "僅更新淨值"; trades_this = []
else:
    if (not s["holdings"]) and (not s["trades"]):
        trades_this = establish(s, prices); action = "初始建倉"
    else:
        action = "僅更新淨值"; trades_this = []

total = total_value(s, prices)
s["snapshots"].append({"ts": now_iso(), "total": round(total,2), "cash": round(s["cash"],2),
                       "equity": round(total-s["cash"],2), "pnl": round(total-CAPITAL,2)})
s["last_run"] = now_iso()
save_state(s)

# ---------- 仪表板数据 ----------
holdings_view = []
for code, h in s["holdings"].items():
    q = quotes.get(code) or {}
    price = q.get("price") or h["avg_cost"]
    prev = q.get("prev_close")
    chg = (price-prev)/prev if prev else None
    mv = h["shares"]*price; cost = h["shares"]*h["avg_cost"]
    holdings_view.append({"code": code, "name": NAME[code], "shares": h["shares"],
        "avg_cost": round(h["avg_cost"],2), "price": round(price,2),
        "src": q.get("source") or "", "chg": chg,
        "high": q.get("high"), "low": q.get("low"), "ts": q.get("ts"),
        "mv": round(mv,2), "w": mv/total if total else 0, "target": PORT[code][0],
        "drift": (mv/total - PORT[code][0]) if total else 0,
        "pnl": round(mv-cost,2), "ret_pct": (mv/cost-1) if cost else 0})
holdings_view.sort(key=lambda x: -x["mv"])

ret_total = (total-CAPITAL)/CAPITAL
DATA = {"asof": now_iso(), "capital": CAPITAL, "cash": round(s["cash"],2),
        "total": round(total,2), "pnl": round(total-CAPITAL,2),
        "ret_pct": ret_total, "equity": round(total-s["cash"],2), "action": action,
        "prices": prices, "src": src, "target": s["target"],
        "holdings": holdings_view, "trades": s["trades"][::-1],
        "snapshots": s["snapshots"], "rebal_thresh": 0.05,
        "rebal_mode": "雙週調倉(執行前先呈報)", "news": news,
        "quotes_api": os.environ.get("QUOTES_API", "")}

# ---------- 仪表板 HTML ----------
CSS = """
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,'Segoe UI','Microsoft YaHei',sans-serif;}
body{background:#f5f7fa;color:#1f2937;padding:28px;line-height:1.6;}
.wrap{max-width:1180px;margin:0 auto;background:#fff;border-radius:14px;box-shadow:0 4px 24px rgba(0,0,0,.06);overflow:hidden;}
.head{background:linear-gradient(120deg,#0f4c81,#1b6ca8);color:#fff;padding:26px 34px;}
.head h1{font-size:22px;font-weight:700;}
.head .sub{margin-top:6px;font-size:13px;opacity:.92;}
.head .meta{margin-top:12px;font-size:12px;background:rgba(255,255,255,.12);display:inline-block;padding:5px 12px;border-radius:20px;}
.ai-tag{display:inline-block;margin-bottom:10px;background:#34d399;color:#064e3b;font-size:12px;font-weight:700;padding:5px 14px;border-radius:20px;}
.sec{padding:22px 34px;border-bottom:1px solid #eef1f5;}
.sec h2{font-size:16px;color:#0f4c81;margin-bottom:12px;display:flex;align-items:center;gap:8px;}
.sec h2::before{content:'';width:5px;height:18px;background:#1b6ca8;border-radius:3px;}
.card-row{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:14px;}
.metric{flex:1;min-width:140px;background:#f8fafc;border:1px solid #e6ebf1;border-radius:10px;padding:13px 15px;}
.metric .v{font-size:21px;font-weight:700;color:#0f4c81;} .metric .v.red{color:#c0392b;} .metric .v.green{color:#1e8449;}
.metric .l{font-size:12px;color:#64748b;margin-top:3px;}
table{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:4px;}
th,td{padding:8px 9px;text-align:right;border-bottom:1px solid #eef1f5;}
th{background:#f1f5f9;color:#334155;font-weight:600;white-space:nowrap;} td.l,th.l{text-align:left;}
tr:hover td{background:#f8fafc;}
.tag{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;background:#e7f0fb;color:#1b6ca8;}
.up{color:#c0392b;font-weight:600;} .down{color:#1e8449;font-weight:600;}
.note{font-size:12px;color:#64748b;} .chart{width:100%;height:320px;}
.box{background:#ecfdf5;border:1px solid #a7f3d0;color:#065f46;border-radius:10px;padding:12px 15px;font-size:13px;margin-top:10px;}
.box.warn{background:#fef2f2;border-color:#f5c6c6;color:#9b2c2c;}
.box.info{background:#eff6ff;border-color:#bfdbfe;color:#1e40af;}
ul{margin:6px 0 6px 20px;font-size:13px;} li{margin:4px 0;}
.disclaimer{background:#334155;color:#cbd5e1;font-size:12px;padding:16px 34px;line-height:1.7;}
.btn{display:inline-block;background:#1b6ca8;color:#fff;border:none;padding:8px 16px;border-radius:8px;font-size:13px;cursor:pointer;font-weight:600;}
.btn:hover{background:#0f4c81;}
#liveStatus{font-size:12px;color:#64748b;margin-left:10px;}
.inp{padding:6px 8px;border:1px solid #cbd5e1;border-radius:7px;font-size:13px;width:100%;box-sizing:border-box;background:#fff;}
.btn.sm{padding:3px 9px;font-size:11px;margin-right:4px;}
.btn.danger{background:#c0392b;} .btn.danger:hover{background:#9b2c2c;}
.news-wrap{display:grid;grid-template-columns:1fr 1fr;gap:14px;}
.news-card{background:#f8fafc;border:1px solid #e6ebf1;border-radius:10px;padding:12px 14px;}
.news-card .nc-h{font-size:13px;color:#0f4c81;margin-bottom:8px;border-bottom:1px solid #e6ebf1;padding-bottom:6px;}
.news-item{font-size:12.5px;padding:5px 0;border-bottom:1px dashed #eef1f5;}
.news-item a{color:#1b6ca8;text-decoration:none;} .news-item a:hover{text-decoration:underline;}
.loggrid{display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end;margin-bottom:6px;}
@media(max-width:760px){.sec{padding:18px;}.tablewrap{overflow-x:auto;}}
"""
JS = """
var D=DATA;
function fmtPct(x){return (x==null?'—':(x*100).toFixed(1)+'%');}
function fmtNum(x){return (x==null?'0':x).toLocaleString('zh-TW',{maximumFractionDigits:0});}
function chgTxt(c){return c==null?'—':((c>=0?'+':'')+(c*100).toFixed(2)+'%');}
var Q={}; D.holdings.forEach(function(h){Q[h.code]={price:h.price,prev_close:h.chg!=null?h.price/(1+h.chg):null};});
function renderHoldings(){
  var hr='';
  D.holdings.forEach(function(h){
    var q=Q[h.code]||{};
    var price=(q.price!=null)?q.price:h.price;
    var prev=(q.prev_close!=null)?q.prev_close:(h.chg!=null?price/(1+h.chg):null);
    var chg=(prev!=null)?(price-prev)/prev:null;
    var pnlc=h.pnl>=0?'up':'down';
    var dc=Math.abs(h.drift)>D.rebal_thresh?'down':'';
    var srcTxt=D.src[h.code]||'';
    hr+='<tr><td class="l"><b>'+h.code+'</b> '+h.name+'</td>'
      +'<td>'+h.shares+'</td>'
      +'<td>'+fmtNum(h.avg_cost)+'</td>'
      +'<td>'+fmtNum(price)+' <span class="note">'+srcTxt+'</span></td>'
      +'<td class="'+(chg>=0?'up':'down')+'">'+chgTxt(chg)+'</td>'
      +'<td class="'+(h.ret_pct>=0?'up':'down')+'">'+(h.ret_pct>=0?'+':'')+fmtPct(h.ret_pct)+'</td>'
      +'<td>'+fmtNum(h.mv)+'</td>'
      +'<td>'+fmtPct(h.w)+'</td><td>'+fmtPct(h.target)+'</td>'
      +'<td class="'+dc+'">'+(h.drift>=0?'+':'')+fmtPct(h.drift)+'</td>'
      +'<td class="'+pnlc+'">'+(h.pnl>=0?'+':'')+fmtNum(h.pnl)+'</td></tr>';
  });
  document.getElementById('holdRows').innerHTML=hr||'<tr><td class="l" colspan="11">尚無持倉</td></tr>';
}
renderHoldings();
var cv=D.holdings.map(function(h){return {name:h.code+' '+h.name,value:+h.mv.toFixed(0)};});
cv.push({name:'現金緩衝',value:+D.cash.toFixed(0)});
var pie=echarts.init(document.getElementById('pie'));
pie.setOption({tooltip:{trigger:'item',formatter:function(p){return p.name+'<br>'+(p.value/D.total*100).toFixed(1)+'%';}},
  legend:{bottom:0,textStyle:{fontSize:11}},
  series:[{type:'pie',radius:['42%','70%'],center:['50%','44%'],label:{formatter:'{b}\\n{d}%',fontSize:11},data:cv}]});
var sn=D.snapshots;
var nav=echarts.init(document.getElementById('nav'));
nav.setOption({tooltip:{trigger:'axis',formatter:function(pp){var i=pp[0].dataIndex;var s=sn[i];return s.ts+'<br>總資產 '+fmtNum(s.total)+'<br>淨利 '+(s.pnl>=0?'+':'')+fmtNum(s.pnl);}},
  grid:{left:65,right:20,top:20,bottom:50},
  xAxis:{type:'category',data:sn.map(function(s){return s.ts.slice(5,16);}),axisLabel:{fontSize:10,rotate:20}},
  yAxis:{scale:true,name:'元',splitLine:{lineStyle:{color:'#eef1f5'}}},
  series:[{type:'line',data:sn.map(function(s){return +s.total.toFixed(0);}),smooth:true,
    itemStyle:{color:'#0f4c81'},areaStyle:{color:'rgba(27,108,168,.12)'},
    label:{show:true,position:'top',fontSize:10,formatter:function(p){return fmtNum(p.value);}}}]});
var tr='';
D.trades.forEach(function(t){
  var badge=t.side==='buy'?'<span class="tag" style="background:#fde8e8;color:#c0392b">買</span>':'<span class="tag" style="background:#e6f7ee;color:#1e8449">賣</span>';
  tr+='<tr><td class="l">'+t.ts.slice(0,16)+'</td><td class="l">'+badge+' <b>'+t.code+'</b> '+t.name+'</td>'
    +'<td>'+t.shares+'</td><td>'+fmtNum(t.price)+'</td><td>'+fmtNum(t.amount)
    +'</td><td>'+fmtNum(t.fee)+(t.tax?(' +稅'+fmtNum(t.tax)):'')+'</td><td class="l note">'+t.note+'</td></tr>';
});
document.getElementById('tradeRows').innerHTML=tr||'<tr><td class="l" colspan="7">尚無交易</td></tr>';
window.addEventListener('resize',function(){pie.resize();nav.resize();});
function applyQuotes(data){
  var qs=(data&&data.quotes)?data.quotes:{};
  var n=0;
  Object.keys(qs).forEach(function(code){
    var q=qs[code];
    if(q&&q.price!=null){Q[code]={price:+q.price, prev_close:(q.prev_close!=null?+q.prev_close:null)}; n++;}
  });
  renderHoldings();
  return n;
}
function refreshLive(){
  var status=document.getElementById('liveStatus');
  status.textContent='抓取实时行情中…';
  if(!D.holdings.length){status.textContent='無持倉';return;}
  var cloudApi=(D.quotes_api||'').trim();
  if(cloudApi){
    fetch(cloudApi,{cache:'no-store'}).then(function(r){return r.json();}).then(function(data){
      if(data&&data.error){status.textContent='云端报价失败：'+(data.error||'');return;}
      var n=applyQuotes(data);
      status.textContent='已更新 '+n+' 檔 · 云端实时 ('+new Date().toLocaleTimeString()+')'+(data.asof?(' · 源 '+data.asof):'');
    }).catch(function(e){
      status.textContent='云端报价连接失败，请检查 QUOTES_API 配置';
    });
    return;
  }
  var isLocalProxy=(location.hostname==='127.0.0.1'||location.hostname==='localhost');
  if(isLocalProxy){
    fetch('/api/quotes',{cache:'no-store'}).then(function(r){return r.json();}).then(function(data){
      if(data&&data.error){status.textContent='代理報價失敗：'+(data.error||'');return;}
      var n=applyQuotes(data);
      status.textContent='已更新 '+n+' 檔 · 本地代理实时 ('+new Date().toLocaleTimeString()+')'+(data.asof?(' · 源 '+data.asof):'');
    }).catch(function(e){
      status.textContent='本地代理連線失敗，請確認伺服器運行中；或發「刷新」讓我（AI）後台重抓';
    });
  } else {
    // 云端托管页面的回退：尽力直連 Yahoo（通常被 CORS 阻擋）
    var codes=D.holdings.map(function(h){return h.code;});
    var pending=codes.length, ok=0;
    codes.forEach(function(code){
      var u='https://query1.finance.yahoo.com/v8/finance/chart/'+code+'.TW?interval=1m&range=1d';
      fetch(u,{mode:'cors'}).then(function(r){return r.json();}).then(function(j){
        var m=j.chart.result[0].meta; var p=m.regularMarketPrice, pc=m.previousClose;
        if(p!=null){Q[code]={price:+p, prev_close:pc!=null?+pc:null}; ok++;}
      }).catch(function(e){})
      .then(function(){pending--; if(pending===0){renderHoldings();
        status.textContent = ok ? ('已更新 '+ok+' 檔 · Yahoo 直連 ('+new Date().toLocaleTimeString()+')')
          : '瀏覽器直連被 CORS 限制，建議配置云端报价源（QUOTES_API），或由 AI 後台重抓嵌入';}});
    });
  }
}
document.getElementById('refreshBtn').addEventListener('click', refreshLive);
var autoTimer=null;
document.getElementById('autoChk').addEventListener('change', function(){
  if(this.checked){ autoTimer=setInterval(refreshLive, 30000); refreshLive();
    document.getElementById('liveStatus').textContent='已開啟自動刷新(30秒)'; }
  else { if(autoTimer) clearInterval(autoTimer); autoTimer=null;
    document.getElementById('liveStatus').textContent='已關閉自動刷新'; }
});
// ===== ③ 可编辑交易日志 (localStorage) =====
var LS_KEY='ai_sim_trade_log_v1';
function loadLog(){ try{return JSON.parse(localStorage.getItem(LS_KEY));}catch(e){return null;} }
function saveLogArr(a){ localStorage.setItem(LS_KEY, JSON.stringify(a)); }
function seedLog(){
  if(loadLog()) return;
  var seed=D.trades.map(function(t){return {id:'seed_'+t.ts, date:t.ts.slice(0,10), code:t.code, side:t.side, shares:t.shares, price:t.price, fee:t.fee, note:t.note};});
  saveLogArr(seed);
}
var editingId=null;
function renderLog(){
  var arr=loadLog()||[];
  var f=document.getElementById('logFilter').value;
  var rows=arr.filter(function(r){return f==='ALL'||r.code===f;});
  var html='';
  rows.forEach(function(r){
    var badge=r.side==='buy'?'<span class="tag" style="background:#fde8e8;color:#c0392b">買</span>':'<span class="tag" style="background:#e6f7ee;color:#1e8449">賣</span>';
    html+='<tr data-id="'+r.id+'"><td class="l">'+r.date+'</td><td class="l">'+badge+' <b>'+r.code+'</b></td><td>'+r.shares+'</td><td>'+fmtNum(r.price)+'</td><td>'+fmtNum(r.shares*r.price)+'</td><td>'+fmtNum(r.fee||0)+'</td><td class="l note">'+(r.note||'')+'</td><td><button class="btn sm edit-btn">編輯</button> <button class="btn sm danger del-btn">刪除</button></td></tr>';
  });
  document.getElementById('logRows').innerHTML=html||'<tr><td class="l" colspan="9">尚無紀錄</td></tr>';
  var buy=0,sell=0,net=0;
  arr.forEach(function(r){ if(r.side==='buy'){buy+=r.shares*r.price;} else {sell+=r.shares*r.price;} net+=(r.side==='buy'?r.shares:-r.shares); });
  document.getElementById('sumBuy').textContent=fmtNum(buy);
  document.getElementById('sumSell').textContent=fmtNum(sell);
  document.getElementById('sumNet').textContent=fmtNum(buy-sell);
  document.getElementById('sumShares').textContent=fmtNum(net);
}
function addLog(){
  var date=document.getElementById('f_date').value||new Date().toISOString().slice(0,10);
  var code=document.getElementById('f_code').value;
  var side=document.getElementById('f_side').value;
  var shares=parseInt(document.getElementById('f_shares').value)||0;
  var price=parseFloat(document.getElementById('f_price').value)||0;
  var fee=parseFloat(document.getElementById('f_fee').value)||0;
  var note=document.getElementById('f_note').value||'';
  if(shares<=0||price<=0){ alert('請填寫有效的股數與價格'); return; }
  var arr=loadLog()||[];
  if(editingId){
    var r=arr.find(function(x){return x.id===editingId;});
    if(r){r.date=date;r.code=code;r.side=side;r.shares=shares;r.price=price;r.fee=fee;r.note=note;}
    saveLogArr(arr); editingId=null; document.getElementById('saveLog').textContent='＋ 新增';
  } else {
    arr.push({id:'L'+Date.now().toString(36)+Math.floor(Math.random()*1e6).toString(36), date:date, code:code, side:side, shares:shares, price:price, fee:fee, note:note});
    saveLogArr(arr);
  }
  renderLog();
  document.getElementById('f_shares').value=''; document.getElementById('f_price').value=''; document.getElementById('f_fee').value=''; document.getElementById('f_note').value='';
}
function editLog(id){
  var arr=loadLog()||[]; var r=arr.find(function(x){return x.id===id;}); if(!r) return;
  editingId=id;
  document.getElementById('f_date').value=r.date;
  document.getElementById('f_code').value=r.code;
  document.getElementById('f_side').value=r.side;
  document.getElementById('f_shares').value=r.shares;
  document.getElementById('f_price').value=r.price;
  document.getElementById('f_fee').value=r.fee||0;
  document.getElementById('f_note').value=r.note||'';
  document.getElementById('saveLog').textContent='✓ 更新';
}
function delLog(id){
  if(!confirm('確定刪除此筆交易紀錄？')) return;
  var arr=(loadLog()||[]).filter(function(x){return x.id!==id;});
  saveLogArr(arr); renderLog();
}
function clearLog(){
  if(!confirm('確定清空所有「我的交易紀錄」？（AI 引擎自動成交不影響）')) return;
  localStorage.removeItem(LS_KEY); renderLog();
}
// ===== ④ 產業新聞 =====
var NAME_MAP={}; D.holdings.forEach(function(h){NAME_MAP[h.code]=h.name;});
function buildNewsList(){
  return Object.keys(D.news).map(function(code){ return {code:code, name:NAME_MAP[code]||code, items:D.news[code]||[]}; });
}
function renderNews(list){
  var html='';
  list.forEach(function(n){
    html+='<div class="news-card"><div class="nc-h">'+n.code+' '+n.name+'</div>';
    if(!n.items.length){ html+='<div class="note">近 5 日無新聞</div>'; }
    n.items.forEach(function(it){
      var title=it.title||'(無標題)';
      var link=it.link?('<a href="'+it.link+'" target="_blank" rel="noopener">'+title+' ↗</a>'):title;
      html+='<div class="news-item">'+(it.date?('['+it.date+'] '):'')+link+(it.source?(' <span class="note">· '+it.source+'</span>'):'')+'</div>';
    });
    html+='</div>';
  });
  document.getElementById('newsWrap').innerHTML=html;
}
function fetchNewsOne(code, day){
  var p=new URLSearchParams({dataset:'TaiwanStockNews',data_id:code,start_date:day});
  return fetch('https://api.finmindtrade.com/api/v4/data?'+p.toString(),{cache:'no-store'})
    .then(function(r){return r.json();})
    .then(function(j){ var rows=j.data||[]; return rows.slice(0,8).map(function(x){return {date:x.date,title:x.title||x.news_title||x.description||'',source:x.source,link:x.link};}); })
    .catch(function(){ return null; });
}
function refreshNews(){
  var st=document.getElementById('newsStatus'); st.textContent='抓取新聞中…';
  var codes=Object.keys(D.news); var pending=codes.length;
  codes.forEach(function(code){
    fetchNewsOne(code, new Date().toISOString().slice(0,10)).then(function(items){
      if(items&&items.length){ D.news[code]=items; }
    }).then(function(){ pending--; if(pending===0){ renderNews(buildNewsList()); st.textContent='已更新 '+codes.length+' 檔新聞 ('+new Date().toLocaleTimeString()+') · FinMind 即時'; } });
  });
}
// ===== init 可编辑日志 & 新聞 =====
(function initLogNews(){
  var cf=document.getElementById('f_code');
  var cfo=''; D.holdings.forEach(function(h){ cfo+='<option value="'+h.code+'">'+h.code+' '+h.name+'</option>'; }); cfo+='<option value="其他">其他</option>'; cf.innerHTML=cfo;
  document.getElementById('f_date').value=new Date().toISOString().slice(0,10);
  var lf=document.getElementById('logFilter');
  var lfo='<option value="ALL">全部</option>'; D.holdings.forEach(function(h){ lfo+='<option value="'+h.code+'">'+h.code+' '+h.name+'</option>'; }); lf.innerHTML=lfo;
  lf.addEventListener('change', renderLog);
  document.getElementById('saveLog').addEventListener('click', addLog);
  document.getElementById('clearLog').addEventListener('click', clearLog);
  document.getElementById('logRows').addEventListener('click', function(e){
    var tr=e.target.closest('tr'); if(!tr) return; var id=tr.getAttribute('data-id'); if(!id) return;
    if(e.target.classList.contains('edit-btn')) editLog(id);
    else if(e.target.classList.contains('del-btn')) delLog(id);
  });
  seedLog(); renderLog();
  var nf=document.getElementById('newsFilter');
  var nfo='<option value="ALL">全部</option>'; D.holdings.forEach(function(h){ nfo+='<option value="'+h.code+'">'+h.code+' '+h.name+'</option>'; }); nf.innerHTML=nfo;
  nf.addEventListener('change', function(){ var v=this.value; renderNews(v==='ALL'?buildNewsList():buildNewsList().filter(function(n){return n.code===v;})); });
  document.getElementById('newsBtn').addEventListener('click', refreshNews);
  renderNews(buildNewsList());
})();
"""
HTML = """<!DOCTYPE html><html lang='zh-Hant'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>AI 模拟投资仪表板（台股·10万·30%回撤）</title>
<style>__CSS__</style>
<script src='https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js'></script>
</head><body><div class='wrap'>
<div class='head'>
  <span class='ai-tag'>★ AI 模拟投资 · 被动执行 30% 回撤容忍计划</span>
  <h1>台股 10 万元 · AI 模拟投资仪表板</h1>
  <div class='sub'>由 AI 抓实时行情、双週調倉（执行前先呈报）、自动记账 ｜ 数据：证交所官方盘 + Yahoo Finance</div>
  <div class='meta'>更新 __ASOF__ ｜ 本次动作：__ACTION__ ｜ 共 __NTRADE__ 笔交易</div>
</div>
<div class='sec'>
  <h2>① 组合概览</h2>
  <div class='card-row'>
    <div class='metric'><div class='v'>__TOTAL__</div><div class='l'>总资产(元)</div></div>
    <div class='metric'><div class='v __PNL_C__'>__PNL__</div><div class='l'>累计盈亏(元)</div></div>
    <div class='metric'><div class='v __RET_C__'>__RET__</div><div class='l'>我的报酬率</div></div>
    <div class='metric'><div class='v'>__CASH__</div><div class='l'>现金缓冲(元)</div></div>
    <div class='metric'><div class='v'>__CASHPC__</div><div class='l'>现金比例</div></div>
    <div class='metric'><div class='v'>__EQUITY__</div><div class='l'>权益市值(元)</div></div>
  </div>
  <div class='grid2' style='display:grid;grid-template-columns:1fr 1fr;gap:18px'>
    <div><div id='pie' class='chart'></div></div>
    <div><div id='nav' class='chart'></div><div class='note' style='text-align:center'>净值走势（每次运行记录一笔）</div></div>
  </div>
</div>
<div class='sec'>
  <h2>② 持仓 · 实时行情与报酬率
    <button id='refreshBtn' class='btn' style='margin-left:auto'>🔄 刷新实时行情</button>
    <label class='note' style='margin-left:10px'><input type='checkbox' id='autoChk'> 自动刷新(30s)</label>
    <span id='liveStatus' class='note'></span>
  </h2>
  <div class='tablewrap'>
  <table><thead><tr><th class='l'>标的</th><th>股数</th><th>成本</th><th>实时价(来源)</th><th>今日涨跌</th><th>我的报酬率</th><th>市值</th><th>实际权重</th><th>目标</th><th>漂移</th><th>盈亏</th></tr></thead>
  <tbody id='holdRows'></tbody></table>
  </div>
  <div class='box info'>「今日涨跌」以昨收为基准；「我的报酬率」= (现价×股数 − 成本×股数) / 成本。<b>实时价支持三种来源，按优先级自动选择：</b>① 云端报价 API（配置 QUOTES_API 后任意环境可用，GitHub Pages/云端托管推荐）② 本地代理服务器 live_server.py（127.0.0.1/localhost 打开时）③ 兜底浏览器直连 Yahoo（受 CORS 限制，仅尽力而为）。页面当前嵌入的价格为最近一次引擎运行的快照，点「🔄 刷新」可拉取最新。</div>
</div>
<div class='sec'>
  <h2>③ 我的交易日志（可编辑）</h2>
  <div class='box info'>本日志存在你的浏览器（localStorage），可随时<b>新增 / 編輯 / 刪除</b>，並用「分篇筛选」按标的查看；自動彙算買入/賣出/淨投入/淨股數。首次開啟會把 AI 引擎的建倉記錄匯入，之後你可自由修改（不會影響引擎的模擬持倉）。⚠️ 僅存於本瀏覽器，換裝置或清快取會遺失，重要紀錄請另行備份。</div>
  <div class='loggrid'>
    <div style='flex:1;min-width:150px'><label class='note'>日期</label><br><input id='f_date' type='date' class='inp'></div>
    <div style='flex:1;min-width:140px'><label class='note'>标的</label><br><select id='f_code' class='inp'></select></div>
    <div style='flex:1;min-width:100px'><label class='note'>方向</label><br><select id='f_side' class='inp'><option value='buy'>买</option><option value='sell'>卖</option></select></div>
    <div style='flex:1;min-width:90px'><label class='note'>股数</label><br><input id='f_shares' type='number' class='inp' placeholder='1-999'></div>
    <div style='flex:1;min-width:100px'><label class='note'>价格</label><br><input id='f_price' type='number' step='0.01' class='inp'></div>
    <div style='flex:1;min-width:90px'><label class='note'>手续费</label><br><input id='f_fee' type='number' class='inp' placeholder='0'></div>
    <div style='flex:2;min-width:170px'><label class='note'>备注</label><br><input id='f_note' type='text' class='inp' placeholder='选填'></div>
    <div><button id='saveLog' class='btn'>＋ 新增</button></div>
  </div>
  <div style='margin:8px 0 12px'>
    <label class='note'>分篇筛选：</label>
    <select id='logFilter' class='inp' style='width:auto'></select>
    <button id='clearLog' class='btn sm danger' style='margin-left:8px'>清空我的日志</button>
  </div>
  <div class='card-row'>
    <div class='metric'><div class='v' id='sumBuy'>0</div><div class='l'>累计买入(元)</div></div>
    <div class='metric'><div class='v' id='sumSell'>0</div><div class='l'>累计卖出(元)</div></div>
    <div class='metric'><div class='v' id='sumNet'>0</div><div class='l'>净投入(元)</div></div>
    <div class='metric'><div class='v' id='sumShares'>0</div><div class='l'>净股数</div></div>
  </div>
  <div class='tablewrap'>
    <table><thead><tr><th class='l'>日期</th><th class='l'>标的</th><th>方向</th><th>股数</th><th>价格</th><th>金额</th><th>手续费</th><th class='l'>备注</th><th>操作</th></tr></thead>
    <tbody id='logRows'></tbody></table>
  </div>
  <h3 style='margin:18px 0 8px;font-size:14px;color:#334155'>AI 引擎自动成交（只读 · 模拟）</h3>
  <div class='tablewrap'>
    <table><thead><tr><th class='l'>时间</th><th class='l'>标的</th><th>股数</th><th>价格</th><th>金额</th><th>手续费/税</th><th class='l'>备注</th></tr></thead>
    <tbody id='tradeRows'></tbody></table>
  </div>
</div>
<div class='sec'>
  <h2>④ 产业新闻
    <button id='newsBtn' class='btn' style='margin-left:auto'>🔄 刷新新闻</button>
    <span id='newsStatus' class='note'></span>
  </h2>
  <div style='margin:4px 0 12px'>
    <label class='note'>按标的筛选：</label>
    <select id='newsFilter' class='inp' style='width:auto'></select>
  </div>
  <div class='box info'>新闻来源：FinMind <code>TaiwanStockNews</code>（免 token、已确认支持浏览器跨域）。抓取各档最新交易日新闻；点「刷新新闻」由浏览器直连 FinMind 即時拉取。僅供參考，請自行核實準確性。</div>
  <div id='newsWrap' class='news-wrap'></div>
</div>
<div class='sec'>
  <h2>⑤ 运行说明</h2>
  <ul>
    <li><b>实时价来源：</b>股票走证交所官方盘 (mis.twse.com.tw) 即时价；ETF(00919)与回退走 Yahoo Finance。盘后/休市时回退到上次价或 FinMind 收盘价。</li>
    <li><b>双週調倉（执行前先呈报）：</b>每兩週由 AI 计算调仓提案（买/卖哪些、股数、金额），<b>先呈报给你确认，绝不擅自交易</b>；你同意后我才执行。</li>
    <li><b>手续费：</b>买入 0.1425%(最低20)；卖出加 0.3% 证交税(ETF 0.1%)。</li>
    <li><b>定时任务：</b>每个交易日自动刷新净值/实时价；每兩週自动生成调仓提案并通知你。</li>
  </ul>
</div>
<div class='disclaimer'>
免责声明：本仪表板为「AI 模拟投资（纸面交易）」演示，所有交易均为虚拟、不涉及真实资金；价格取自公开实时源，存在延迟/误差，非下单依据或投资建议。市场有风险，投资需谨慎；过往表现不预示未来收益。
</div>
</div>
<script>var DATA = __DATA_JSON__; __JS__</script>
</body></html>"""

HTML = (HTML.replace("__CSS__", CSS).replace("__JS__", JS)
        .replace("__DATA_JSON__", json.dumps(DATA, ensure_ascii=False))
        .replace("__ASOF__", DATA["asof"]).replace("__ACTION__", DATA["action"])
        .replace("__NTRADE__", str(len(DATA["trades"])))
        .replace("__TOTAL__", fmtNum(total)).replace("__CASH__", fmtNum(s["cash"]))
        .replace("__EQUITY__", fmtNum(total-s["cash"]))
        .replace("__PNL__", ("+" if total>=CAPITAL else "")+fmtNum(total-CAPITAL))
        .replace("__PNL_C__", "red" if total>=CAPITAL else "green")
        .replace("__RET__", ("+" if ret_total>=0 else "")+fmtPct(ret_total))
        .replace("__RET_C__", "red" if ret_total>=0 else "green")
        .replace("__CASHPC__", fmtPct(s["cash"]/CAPITAL)))

out = os.path.join(HERE, "portfolio_dashboard.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(HTML)
check = "var DATA=" + json.dumps(DATA, ensure_ascii=False) + ";\n" + JS
with open(os.path.join(HERE, "_check_dash.js"), "w", encoding="utf-8") as f:
    f.write(check)

print(f"[{action}] total={round(total)} cash={round(s['cash'])} pnl={round(total-CAPITAL)} ret={fmtPct(ret_total)} equity={round(total-s['cash'])}")
print("quotes:", {k: (round(v['price'],1), v['source']) for k,v in quotes.items()})
print("trades this run:", len(trades_this), "| total trades:", len(s["trades"]))
print("holdings:", {c: h["shares"] for c,h in s["holdings"].items()})
