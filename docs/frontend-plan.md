# finget 数据展示前端 — 规划方案

> 创建日期：2025-07-17
> 状态：规划中，待确认后执行

---

## 一、数据全景（11 张表，5 大类）

| 类别 | 表名 | 核心字段 | 数据量级 |
|------|------|---------|---------|
| **K线行情** | `daily`, `weekly` | OHLCV + 涨跌幅 | 百万级（5400只 × 250天/年） |
| **基本面指标** | `daily_basic`, `adj_factor`, `stk_factor_pro` | PE/PB/市值/复权因子/技术指标 | 百万级 |
| **标的信息** | `stock_basic`, `hk_us_basic` | 股票代码/名称/行业/上市日期 | 万级 |
| **研究数据** | `report_rc`, `broker_recommend` | 研报/盈利预测/评级/金股 | 十万级 |
| **另类数据** | `stk_surv` + `stk_surv_detail` | 机构调研/接待/调研内容 | 万级 |
| **基础设施** | `trade_cal` | 交易日历 | 万级 |

---

## 二、技术栈推荐

### 方案 A（推荐）：FastAPI + 原生 HTML/JS + 专业图表库

```
架构:  DuckDB ──→ FastAPI ──→ 浏览器
                    │
                    ├── /api/kline/{ts_code}       (日线/周线)
                    ├── /api/stock/{ts_code}        (基本面)
                    ├── /api/research/{ts_code}     (研报)
                    ├── /api/survey/{ts_code}       (调研)
                    ├── /api/broker_recommend       (金股)
                    ├── /api/stocks/search          (股票搜索)
                    └── /api/stats/overview         (总览)
```

| 层 | 技术 | 理由 |
|---|------|------|
| **后端** | FastAPI + DuckDB 直连 | 零依赖新增，异步高性能，你已有 Python 环境 |
| **K线图** | [TradingView Lightweight Charts](https://github.com/tradingview/lightweight-charts) v5 | 专业级 K线/成交量/指标叠加，免费，<100KB |
| **通用图表** | [ECharts](https://echarts.apache.org/) | 散点/柱状/饼图/热力/关系图全覆盖 |
| **UI 框架** | 原生 HTML + Tailwind CSS + Alpine.js | 极轻量，不引入构建工具链 |
| **部署** | `uv run finget serve` 一键启动 | 集成到 CLI |

### 方案 B（更快但更重）：Streamlit

- 一个 Python 文件搞定全部页面
- 劣势：K线交互体验差，大数据量渲染卡顿，样式受限

**推荐方案 A**，理由：K线图是核心需求，Streamlit 无法提供专业级交互体验。

---

## 三、页面结构设计

```
┌─────────────────────────────────────────────────┐
│  🔍 搜索股票...          [仪表盘] [K线] [研报] [调研] [金股] │  ← 顶部导航
├─────────────────────────────────────────────────┤
│                                                   │
│  页面 1: 仪表盘 (Dashboard)                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────────────┐  │
│  │ 数据总览  │ │ 行业分布  │ │ 近期研报热度     │  │
│  │ 11张表   │ │ 饼图     │ │ Top 10 机构      │  │
│  │ 行数/覆盖 │ │          │ │                  │  │
│  └──────────┘ └──────────┘ └──────────────────┘  │
│                                                   │
│  页面 2: K线分析 (选股后)                          │
│  ┌──────────────────────────────────────────┐    │
│  │  [000001.SZ 平安银行]  [日线▾] [前复权▾]   │    │
│  │  ┌────────────────────────────────────┐  │    │
│  │  │     🕯️ K线 + 均线 + 成交量          │  │    │
│  │  │     TradingView 交互式图表          │  │    │
│  │  └────────────────────────────────────┘  │    │
│  │  ┌─────────┐ ┌─────────┐ ┌──────────┐   │    │
│  │  │ PE Band │ │ MACD    │ │ KDJ/RSI  │   │    │
│  │  └─────────┘ └─────────┘ └──────────┘   │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  页面 3: 研报中心 (选股后)                         │
│  ┌──────────────────────────────────────────┐    │
│  │  卖方研报列表（可筛选机构/评级/日期）      │    │
│  │  ┌────┬──────┬────┬────┬────┬──────┐    │    │
│  │  │日期│ 机构  │评级│EPS │PE  │目标价│    │    │
│  │  ├────┼──────┼────┼────┼────┼──────┤    │    │
│  │  │... │ ...  │... │... │... │ ...  │    │    │
│  │  └────┴──────┴────┴────┴────┴──────┘    │    │
│  │  ┌──────────────────────────────────┐   │    │
│  │  │  EPS 预测趋势图（时序折线）        │   │    │
│  │  └──────────────────────────────────┘   │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  页面 4: 机构调研                                 │
│  ┌──────────────────────────────────────────┐    │
│  │  调研记录时间线 + 接待详情                 │    │
│  │  可展开查看 content 全文                  │    │
│  └──────────────────────────────────────────┘    │
│                                                   │
│  页面 5: 券商金股                                 │
│  ┌──────────────────────────────────────────┐    │
│  │  按月展示各券商推荐金股                     │    │
│  │  ┌──────────────────────────────────┐    │    │
│  │  │  热力图：券商 × 月份 × 推荐频次   │    │    │
│  │  └──────────────────────────────────┘    │    │
│  └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────┘
```

---

## 四、各数据类型 → 展示方式映射

| 数据 | 最佳展示方式 | 图表库 |
|------|-------------|--------|
| **日线/周线 K线** | 🕯️ 蜡烛图 + 成交量柱 + 均线叠加 | TradingView Lightweight Charts |
| **PE/PB/PS 时序** | 📈 折线图（单或多指标叠加） | ECharts |
| **复权因子** | 📈 折线图（展示除权除息节点） | ECharts |
| **技术指标 MACD/KDJ/RSI** | 📊 副图指标面板（K线图下方） | TradingView LW Charts |
| **股票基础信息** | 📋 信息卡片 + 行业分类饼图 | HTML + ECharts |
| **卖方研报** | 📋 可排序表格 + EPS预测趋势折线 | ECharts |
| **券商金股** | 🔥 热力图（券商×月份） + 详情表 | ECharts Heatmap |
| **机构调研** | 🕐 时间线 + 可展开内容卡片 | HTML/CSS |
| **交易日历** | 📅 日历热力图（一年概览） | ECharts Calendar |

---

## 五、分阶段实施建议

| 阶段 | 内容 | 工作量 |
|------|------|--------|
| **Phase 1** | FastAPI 后端 + `/api/` 接口层 + CLI `serve` 命令 | 1-2天 |
| **Phase 2** | 仪表盘首页（数据总览 + 行业分布） | 0.5天 |
| **Phase 3** | K线分析页（TradingView 蜡烛图 + 指标面板） | 1天 |
| **Phase 4** | 研报中心 + 金股热力图 + 调研时间线 | 1天 |
| **Phase 5** | 股票搜索 + 跨页面联动 + 响应式优化 | 0.5天 |

---

## 六、关键技术决策待确认

1. **K线图库**：TradingView Lightweight Charts（免费、专业、轻量）还是 ECharts（国产、文档中文）？
2. **前后端分离程度**：内嵌 HTML 模板（简单）还是 Vue/React SPA（灵活但引入构建工具）？推荐**内嵌模板**起步
3. **CLI 集成**：是 `finget serve` 一个命令启动，还是独立的前端项目？推荐**集成到 CLI**

---

## 七、目录结构规划

```
src/finget/
├── server/
│   ├── __init__.py
│   ├── app.py              # FastAPI 应用入口
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── kline.py        # K线相关接口
│   │   ├── stock.py        # 股票基础信息接口
│   │   ├── research.py     # 研报接口
│   │   ├── survey.py       # 调研接口
│   │   ├── broker.py       # 金股接口
│   │   └── stats.py        # 统计/仪表盘接口
│   └── templates/
│       ├── base.html        # 基础模板 (Tailwind CDN + 布局)
│       ├── dashboard.html   # 仪表盘
│       ├── kline.html       # K线分析
│       ├── research.html    # 研报中心
│       ├── survey.html      # 机构调研
│       └── broker.html      # 券商金股
├── cli.py                   # 新增 `serve` 命令
```

---

## 八、API 接口设计草案

### 仪表盘
```
GET /api/stats/overview          → {tables: [{name, rows, min_date, max_date, stock_count}]}
GET /api/stats/industry_dist     → [{industry, count}]
```

### K线
```
GET /api/kline/daily?ts_code=000001.SZ&start=20240101&end=20241231&adj=qfq
    → [{trade_date, open, high, low, close, volume, ...}]
GET /api/indicators/{ts_code}?start=...&end=...&type=macd,kdj,rsi
```

### 股票
```
GET /api/stocks/search?q=平安       → [{ts_code, name, industry, area}]
GET /api/stocks/{ts_code}           → {基本信息 + 最新指标}
GET /api/stocks/{ts_code}/pe_band   → PE Band 数据
```

### 研报
```
GET /api/research/{ts_code}?start=...&end=...&org=...
    → [{report_date, org_name, rating, eps, pe, max_price, ...}]
GET /api/research/{ts_code}/eps_trend   → EPS 预测时序
```

### 调研
```
GET /api/survey/{ts_code}?start=...&end=...
    → [{surv_date, rece_org, rece_mode, fund_visitors, content?}]
```

### 金股
```
GET /api/broker_recommend?month=202406&broker=...
    → [{month, broker, ts_code, name}]
GET /api/broker_recommend/heatmap?start=202401&end=202412
    → 热力图数据
```
