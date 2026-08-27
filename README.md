# AI 模拟投资仪表板 · 云端部署包

台股 10 万模拟投资仪表板（AI 模拟投资引擎）的云端部署包。
目标：**部署到云端 + 自动实时更新 + AI 后台修改内容**，全部零成本（GitHub 免费额度）。

## 架构一览

```
我的会话（本地 AI 引擎）──重新生成──▶ GitHub 仓库（本包）
                                          │
          ┌───────────────────────────────┤
          │ GitHub Actions（云端定时）     │ GitHub Pages（静态托管）
          │ 每交易日盘中每 30 分钟跑一次   │ https://<你的用户名>.github.io/<仓库名>/
          │ python portfolio_sim.py        │
          │ --update → 提交更新 → Pages 发布│
          └───────────────────────────────┘
                          ▲
                  可选增强：Cloudflare Worker
                  （worker/quotes.js）→ 页面秒级实时刷新
```

- **云端 ✅**：GitHub Pages 公开链接，任意设备可开
- **自动更新 ✅**：GitHub Actions 每个交易日 09:00–14:30（台湾时间）每 30 分钟跑一次引擎，抓实时价、更新净值、重新生成页面并自动部署
- **AI 后台修改 ✅**：调仓提案/执行仍由 AI 引擎负责（每两週 `--propose`，执行前先呈报）；云端 Actions 负责每日净值刷新；我（AI）在本地运行时也可重新生成并提交

## 启用步骤（约 5 分钟，只用浏览器）

1. **新建仓库**：github.com → New repository → 仓库名随意（如 `tw-portfolio-sim`）→ **Public** → Create。
2. **上传文件**：进入仓库 → Add file → Upload files → 把本目录内全部文件拖进去：
   - `portfolio_sim.py`、`portfolio_sim.json`、`index.html`、`portfolio_dashboard.html`
   - `.github/workflows/dashboard.yml`（隐藏文件夹！Windows 需先：打开本文件夹 → 顶部「查看」→ 勾选「隐藏的项目」，才能看到半透明的 `.github`）
   - `.gitignore`、`README.md`、`worker/`（可选增强，可后补）
   → Commit changes。
3. **开启 Pages**：仓库 Settings → Pages → Source 选择 **GitHub Actions** → Save。
4. **手动跑一次**（不用等定时）：仓库 Actions → 左侧 `AI 模拟投资仪表板 · 云端自动更新` → **Run workflow** → 等待两个任务（update → deploy）变绿（约 1–2 分钟）。
5. **打开你的仪表板**：`https://<你的用户名>.github.io/<仓库名>/`
   - 页面内嵌最近一次运行的价格快照；点「🔄 刷新实时行情」在没有云端报价源时会被浏览器 CORS 限制（属预期），有 Cloudflare Worker 后可秒级实时（见下）。

之后：**每个交易日自动更新，无需任何人工操作。**

## （可选增强）秒级实时报价 · Cloudflare Worker

页面「刷新」按钮要真正实时，需要一个带 CORS 的服务端代理（TWSE/Yahoo 不允许浏览器直连）：

1. 注册 cloudflare.com（免费，无需信用卡）→ Workers & Pages → Create → **Create Worker**。
2. 把 `worker/quotes.js` 全部内容粘贴进去 → **Deploy** → 记下地址，形如 `https://xxx.workers.dev`。
3. 回到 GitHub 仓库：Settings → Secrets and variables → Actions → **Variables** → New variable：
   - 名称：`QUOTES_API`
   - 值：`https://xxx.workers.dev`（你的 Worker 地址，末尾无需路径）
4. 再手动 Run workflow 一次 → 页面会检测到云端报价源，点「🔄 刷新」即秒级实时（自动刷新 30 秒也可用）。

> 说明：Worker 免费计划 10 万请求/天，30 秒自动刷新一天约 2 万次请求，够用。

## 注意事项

- **不构成投资建议**：本仪表板为「AI 模拟投资（纸面交易）」演示，所有交易均为虚拟、不涉及真实资金；价格取自公开实时源，存在延迟/误差。
- **数据源**：历史/新闻走 FinMind（免费档）；实时走证交所官方盘 + Yahoo（非官方接口，无 SLA，可能随时变动）。
- **交易日志**：页面内「我的交易日志」存于浏览器 localStorage，换设备会丢失，重要记录请另行备份。
- **调仓**：引擎每两週生成提案、执行前先呈报确认，绝不自动交易；云端 Actions 只跑 `--update`（仅刷新净值），**不会**自动调仓。
