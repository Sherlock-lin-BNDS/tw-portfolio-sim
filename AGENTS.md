# AGENTS.md（云端部署包）

> 本目录是 GitHub Pages 仓库内容，对应「台股 10 万模拟投资仪表板」项目。
> **完整项目上下文见上级目录 `../AGENTS.md`**，本文件只聚焦部署包本身。

## 本目录作用
托管静态仪表板 + 用 GitHub Actions 自动刷新净值并部署。

## 必含文件
- `index.html` / `portfolio_dashboard.html` — 站点页面（`index.html` 由 Actions 用 `cp portfolio_dashboard.html index.html` 生成）
- `portfolio_sim.py` — 引擎（抓实时价 / `--update` 刷净值 / `--propose` 出调仓提案）；本地运行也用同一份逻辑
- `portfolio_sim.json` — 持仓状态
- `.github/workflows/dashboard.yml` — **Actions 工作流（关键）**：每交易日 09:00–14:30 台湾时间每 30 分钟跑 `python portfolio_sim.py --update` → 提交 → Pages 部署
- `worker/quotes.js` — 可选 Cloudflare Worker 代理（秒级实时报价，解决浏览器 CORS）
- `README.md` / `.gitignore`

## 关键约定（别改）
- 实时价：股票走证交所官方盘 `mis.twse.com.tw/stock/api/getStockInfo.jsp?ex_ch=tse_<code>.tw`；ETF(00919)/回退走 Yahoo `query1.finance.yahoo.com/v8/finance/chart/<code>.TW`。FinMind 仅用于历史/除息。
- Actions 只跑 `--update`（刷净值），**不自动调仓**；调仓须人工确认。
- 数据源/数据约定细节见 `../AGENTS.md` §4。

## 已知坑
- GitHub 网页 Upload files **会过滤隐藏文件**（以 `.` 开头）。上传时 `.github/workflows/dashboard.yml` 与 `.gitignore` 不会传上去（显示 "the file is hidden"）。
  - 解决：用 Add file → Create new file 手动建 `.github/workflows/dashboard.yml` 并粘贴本目录同名文件内容；或用 git 命令行 `git add .` 后再 push（git 带隐藏文件）。
- 上传后须到 Settings → Pages → Source 选 **GitHub Actions** 才能启用部署。

## 开启秒级实时（可选）
1. Cloudflare 建 Worker，粘 `worker/quotes.js` 全文 → Deploy，记地址 `https://xxx.workers.dev`。
2. 仓库 Settings → Secrets and variables → Actions → Variables → 新增 `QUOTES_API` = 该地址。
3. 再 Run workflow 一次，`portfolio_sim.py` 会自动把 `QUOTES_API` 注入页面，实现 30 秒自动刷新。
