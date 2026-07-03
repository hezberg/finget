# finget — 金融数据获取、存储与分发系统

一个基于 Python + [uv](https://github.com/astral-sh/uv) 管理的金融数据框架，支持多数据源接入（tushare 等）、DuckDB 时序存储、增量/全量更新策略、数据查漏补缺、实时进度展示与统计。

> **极简上手** — 唯一需要改的配置是 `.env` 里的 `TUSHARE_TOKEN`。

## 架构总览

```
┌──────────────────────────────────────────────────────┐
│                      CLI / API                        │
│                  (finget.cli / read)                  │
├──────────────┬───────────────┬───────────────────────┤
│   Fetcher    │   Updater     │      Reader           │
│  (数据获取)   │  (更新策略)    │    (数据读取)          │
├──────────────┴───────────────┴───────────────────────┤
│                    Storage Layer                      │
│              (DuckDB 时序存储引擎)                      │
├──────────────────────────────────────────────────────┤
│                   Config Layer                        │
│           (Pydantic + .env，无 YAML)                  │
└──────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 环境准备

```bash
# 安装 uv（如未安装）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 创建虚拟环境并安装依赖
uv sync --extra dev
```

### 2. 配置（只需要 token）

```bash
cp .env.example .env
# 编辑 .env，填入 TUSHARE_TOKEN=你的token
```

`.env` 是**唯一**需要改的配置。其他参数（数据库路径、限速、回溯年数、镜像站列表、数据集定义等）都 hardcode 在代码里，按需修改 `src/finget/config.py` 中的 Pydantic 模型默认值。

### 3. 一站式初始化

```bash
# 拉表结构 + 拉全部上市股票 + 拉 SSE/SZSE 交易日历（约 1-2 分钟）
uv run finget init

# 每天定时增量更新（按日全市场拉取，约 10 秒）
uv run finget fetch daily
uv run finget fetch adj_factor
uv run finget fetch daily_basic
uv run finget fetch stk_factor_pro

# 或一次性按策略配置更新多个数据集
uv run finget fetch latest

# 数据查漏补缺（按策略配置）
uv run finget scan
```

### 4. 常用命令速查

| 命令 | 作用 |
|------|------|
| `finget init` | 一站式初始化（建表 + 拉 stock_basic + 拉 trade_cal） |
| `finget init --schema-only` | 仅建表，不拉数据（不需要 TUSHARE_TOKEN） |
| `finget db recreate --confirm` | 删表重建（schema 升级/完全重置） |
| `finget fetch <dataset>` | 拉取数据集（daily_supported 自动按日全市场拉取，不传 -S 默认往前 1 年） |
| `finget fetch latest` | 按 `config.yaml` 配置做每日增量更新 |
| `finget scan` | 按 `config.yaml` 配置查漏补缺 |
| `finget stats` | 数据统计（行数 / 时间范围 / 覆盖率） |
| `finget show <table> -n 10` | 预览表内容（支持 `-o` 导出 CSV） |

### 5. 常用场景示例

```bash
# 从指定日期拉取日线（daily_supported 自动按日全市场拉取）
uv run finget fetch daily -S 20190101 -E 20241231

# 拉取指定日期范围的交易日历
uv run finget fetch trade_cal -S 20240101 -E 20240131

# 只初始化上交所数据
uv run finget init --exchanges SSE

# 拉退市股票（D=退市 / P=暂停 / L=上市）
uv run finget init --list-status D

# 跳过某项
uv run finget init --skip-trade-cal      # 只建表 + 拉股票
uv run finget init --skip-stock-basic    # 只建表 + 拉日历

# 升级 schema（tushare 字段变化时，会清空所有数据！）
uv run finget init --recreate

# 全量拉取研报（按季度并发分批）
uv run finget fetch report_rc -S 20210101 -E 20261231

# 全量拉取券商金股（按月并发分批）
uv run finget fetch broker_recommend -S 20160101 -E 20260630

# 补拉某几天缺失的数据（按日全市场拉取 + 日期范围）
uv run finget fetch adj_factor -S 20241201 -E 20241205
```

### 6. 拉取行为速查

finget 不再暴露"模式"概念，拉取行为由数据集类型自动决定：

| 数据集类型 | 拉取行为 | 速度 | 备注 |
|------|---------|------|------|
| `daily` / `adj_factor` / `daily_basic` / `stk_factor_pro` | **按日全市场拉取** | **极快（~10s/日）** | daily_supported，按 trade_date 单日拉全市场 |
| `weekly` | 逐标的拉取 | 慢（~25 min） | 不支持按日全市场，逐标的 + 回溯 |
| `report_rc` | 短跨度按天 / 长跨度按季度 | 中等 | ≤60天逐日拉，>60天按季度分批并发 |
| `broker_recommend` | 按月份拉取 | 中等 | 按 month 并发分批 |
| `stk_surv` | 逐标的拉取 | 中等 | content 大文本拆 detail 表，按 ts_code 分页（单次100条） |
| `stock_basic` / `trade_cal` / `hk_us_basic` | 一次性全量拉取 | 快 | 不分标的（hk_us_basic 合并 hk_basic+us_basic） |

> `fetch` 命令无需指定模式，行为自动决定。daily_supported 数据集（无 `--codes`）按日全市场拉取；指定 `--codes` 时改为逐标的拉取。`scan` 命令做查漏补缺（对比交易日历补缺失）。

## 项目结构

```
finget/
├── pyproject.toml          # 项目 & 依赖配置（hatchling 构建）
├── uv.lock                 # uv 锁文件
├── README.md               # 本文件
├── AGENTS.md               # AI Agent 项目记忆文件（含架构要点/陷阱/接口速查）
├── .env.example            # 环境变量模板（TUSHARE_TOKEN 等）
├── .env                    # 实际环境变量（gitignore，不提交）
├── .gitignore
├── src/finget/
│   ├── __init__.py
│   ├── logging.py          # loguru 日志配置
│   ├── config.py           # Pydantic 配置（极简版，从 .env 加载）
│   ├── cli.py              # Click CLI（顶级 init/fetch/scan/stats/show + db 子组）
│   ├── fetchers/           # 数据获取层
│   │   ├── base.py         # 抽象基类（限速 + 分页）
│   │   ├── tushare_fetcher.py
│   │   └── progress.py     # rich 进度条
│   ├── storage/            # 持久化层
│   │   └── duckdb_store.py # DuckDB 存储（含幂等 upsert + schema 鲁棒性）
│   ├── updater/            # 更新策略
│   │   └── strategies.py   # 全量 / 增量 / 按日 / 补漏
│   ├── reader/             # 读取层
│   │   └── data_reader.py
│   ├── stats/              # 状态统计
│   │   └── collector.py
└── tests/                  # 单元测试（166 用例，8 个文件）
    ├── conftest.py
    ├── test_config.py
    ├── test_storage.py
    ├── test_fetchers.py
    ├── test_updater.py
    ├── test_reader.py
    ├── test_stats.py
    └── test_cli.py
```

## 数据库 Schema

| 表名 | 类型 | 去重键 | 说明 |
|------|------|--------|------|
| `stock_basic` | 基础 | `ts_code` | 股票基础信息（17 列，含 fullname/enname/cnspell/act_name/act_ent_type） |
| `trade_cal` | 日历 | `exchange, cal_date` | 交易日历（is_open/pretrade_date） |
| `daily` | 时序 | `ts_code, trade_date` | 日线行情 |
| `weekly` | 时序 | `ts_code, trade_date` | 周线行情 |
| `adj_factor` | 时序 | `ts_code, trade_date` | 复权因子 |
| `daily_basic` | 时序 | `ts_code, trade_date` | 每日指标(PE/PB等) |
| `report_rc` | 研报 | `ts_code, report_date, org_name, author_name, quarter` | 卖方盈利预测（22 列） |
| `stk_factor_pro` | 时序 | `ts_code, trade_date` | 技术面因子（MACD/KDJ/RSI/BOLL/EMA/MA 等，约 200 列，含 bfq/hfq/qfq 复权） |
| `broker_recommend` | 券商金股 | `month, broker, ts_code` | 券商月度金股（4 列） |
| `stk_surv` | 机构调研 | `ts_code, surv_date, rece_org` | 机构调研主表（9 列，content 拆 detail 表） |
| `stk_surv_detail` | 机构调研详情 | `ts_code, surv_date, rece_org` | 调研内容详情表（content 大文本隔离） |
| `hk_us_basic` | 港美股基础 | `ts_code` | 港股+美股基础信息（3 列：合并 hk_basic+us_basic） |

`tushare token` 是**唯一必填**的环境变量。`daily` / `adj_factor` / `daily_basic` / `stk_factor_pro` 支持按日全市场拉取（极速）。策略配置文件 `config.yaml`（可选）控制 `fetch latest` / `scan` 处理哪些数据集。

## 存储方案选型：DuckDB

本项目选用 [DuckDB](https://duckdb.org/) 作为存储引擎，理由：

| 特性 | DuckDB | SQLite | Parquet | ClickHouse |
|------|--------|--------|---------|------------|
| 列式存储 | ✅ | ❌ | ✅ | ✅ |
| OLAP 分析性能 | ✅ 强 | ❌ 弱 | ✅ | ✅ |
| 嵌入式（无服务端） | ✅ | ✅ | ✅ | ❌ |
| 零运维 | ✅ | ✅ | ✅ | ❌ |
| SQL 支持 | ✅ 完整 | ✅ | 部分 | ✅ |
| Python 集成 | ✅ 极佳 | ✅ | ✅ | 一般 |
| 单文件可移植 | ✅ | ✅ | ✅ | ❌ |

**结论**：DuckDB 兼具嵌入式零运维与列式 OLAP 性能，单文件 `.duckdb` 可直接拷贝迁移，非常适合金融时序数据分析场景。

## Python API 示例

```python
from finget.config import get_config
from finget.fetchers.tushare_fetcher import TushareFetcher
from finget.storage import DuckDBStore
from finget.updater.strategies import UpdateStrategy
from finget.reader import DataReader

cfg = get_config()                  # 从 .env 加载，缺 token 抛 ValueError
fetcher = TushareFetcher(cfg.fetcher.tushare)
store = DuckDBStore(cfg.storage)

# 初始化表
store.init_all()

# 协调器：拉取数据（行为由数据集类型自动决定，无需指定模式）
strategy = UpdateStrategy(fetcher, store, cfg)
strategy.run(ds_daily, start_date="20190101", end_date="20241231")  # 按日全市场（daily_supported）
strategy.run(ds_daily)                                              # 增量：start_date=None 自动查 max_date 回溯
strategy.run_scan(ds_daily)                                         # 查漏补缺

# 读取层
reader = DataReader(store)
df = reader.get_kline("000001.SZ", start_date="20240101", end_date="20240131")
close = reader.get_close("000001.SZ")
basic = reader.get_stock_basic(industry="银行")
factor = reader.get_stk_factor(ts_code="000001.SZ")   # 技术面因子
```

## 已知限制

1. **交易日历**：`SCAN` 模式依赖 tushare `trade_cal` 接口，非 tushare 数据源无法自动查漏
2. **DuckDB 并发**：单写入者模型，不支持多进程并发写入同一文件
3. **数据源扩展**：目前仅实现 tushare，新增数据源需继承 `BaseFetcher`
4. **增量更新容错**：当前回溯 `incremental_lookback_days` 天，对停牌/节假日可能冗余拉取
5. **tushare 字段升级**：当 tushare 增加新字段时，库表 schema 可能与 API 不一致，建议用 `finget init --recreate` 重建（`upsert()` 自身已具备 schema 差异容错，但显式升级更干净）

## 开发

```bash
uv run pytest                          # 跑全部测试（166 用例）
uv run pytest --cov=finget             # 带覆盖率
uv run ruff check src/ tests/          # lint
uv run ruff format src/                # 格式化
uv run mypy src/finget                 # 类型检查
uv build                               # 构建 wheel + sdist
uv pip install -e .                    # 开发模式安装
```

更多架构要点、跨文件设计权衡、API 速查 → 请阅读 [`AGENTS.md`](./AGENTS.md)。
