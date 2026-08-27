// Cloudflare Worker · 台股实时报价代理
// 用途：让云端托管的仪表板页面（GitHub Pages 等）可以实时刷新行情，
//       解决 TWSE / Yahoo 不发 CORS 头、浏览器无法直连的问题。
// 部署：Cloudflare 控制台 → Workers & Pages → 创建 Worker → 粘贴本文件 → 部署
//       → 得到形如 https://xxx.workers.dev 的地址，再配合 QUOTES_API 使用。
// 免费计划：10 万请求/天，个人看盘完全够用。

const CODES = ['0050', '2330', '2454', '2357', '00919', '2412', '2884'];
const YAHOO_ONLY = new Set(['00919']); // TPEx ETF 走 Yahoo
const CORS = {
  'Access-Control-Allow-Origin': '*',
  'Content-Type': 'application/json;charset=utf-8',
  'Cache-Control': 'no-store',
};

export default {
  async fetch(request) {
    if (request.method === 'OPTIONS') return new Response(null, { headers: CORS });
    const out = {};
    await Promise.all(CODES.map(async (code) => {
      try {
        let q = null;
        if (YAHOO_ONLY.has(code)) q = await yahoo(code);
        else { q = await twse(code); if (!q) q = await yahoo(code); }
        if (q && q.price != null) out[code] = q;
      } catch (e) { /* 单档失败跳过，不影响其它档 */ }
    }));
    return new Response(JSON.stringify({ asof: new Date().toISOString(), quotes: out }), { headers: CORS });
  },
};

async function twse(code) {
  const url = `https://mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_${code}.tw`;
  const r = await fetch(url, {
    headers: { 'User-Agent': 'Mozilla/5.0', Referer: 'https://mis.twse.com.tw/' },
  });
  const j = await r.json();
  const m = (j.msgArray || [])[0];
  if (!m) return null;
  let price = (m.z && m.z !== '-') ? parseFloat(m.z) : null;
  if (price == null) {
    const b = parseFloat((m.b || '').split('_')[0]);
    const a = parseFloat((m.a || '').split('_')[0]);
    if (b && a) price = (b + a) / 2;
  }
  if (price == null) return null;
  return {
    price,
    prev_close: (m.y && m.y !== '-') ? parseFloat(m.y) : null,
    high: (m.h && m.h !== '-') ? parseFloat(m.h) : null,
    low: (m.l && m.l !== '-') ? parseFloat(m.l) : null,
    source: 'TWSE即時',
    ts: m.t,
  };
}

async function yahoo(code) {
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${code}.TW?interval=1m&range=1d`;
  const r = await fetch(url, { headers: { 'User-Agent': 'Mozilla/5.0' } });
  const j = await r.json();
  const meta = j.chart && j.chart.result && j.chart.result[0] && j.chart.result[0].meta;
  if (!meta) return null;
  const p = meta.regularMarketPrice;
  if (p == null) return null;
  return {
    price: parseFloat(p),
    prev_close: meta.previousClose != null ? parseFloat(meta.previousClose) : null,
    high: meta.regularMarketDayHigh != null ? parseFloat(meta.regularMarketDayHigh) : null,
    low: meta.regularMarketDayLow != null ? parseFloat(meta.regularMarketDayLow) : null,
    source: 'Yahoo',
    ts: meta.regularMarketTime != null ? meta.regularMarketTime : null,
  };
}
