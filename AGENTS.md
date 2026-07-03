# AGENTS.md — finget 项目记忆文件

> 本文件供 AI Agent 快速理解项目全貌。修改代码前请先阅读此文件。
> 详细变更历史见 [`CHANGELOG.md`](./CHANGELOG.md)。

## 0. 工具使用约定

### Context7 MCP（库/框架文档查询）

**何时用**：用户询问任何库、框架、SDK、API、CLI 工具或云服务时（即使是知名的 React、Next.js、Prisma、Express、Tailwind、Django、Spring Boot 等），都用 Context7 MCP 拉最新文档。包括：API 语法、配置、版本迁移、库特定调试、安装指南、CLI 用法。**即使你认为知道答案也要拉**——训练数据可能过期。优先用 Context7 而非 web search 查库文档。

**何时不用**：重构、从零写脚本、调试业务逻辑、代码审查、一般编程概念。

**步骤**：
1. 用 `resolve-library-id` 输入库名 + 用户问题（除非用户给了 `/org/project` 格式的精确 ID）
2. 按 精确名匹配 / 描述相关性 / 代码片段数 / 来源信誉（High/Medium 优先）/ benchmark 分数（越高越好） 选最佳匹配（ID 格式 `/org/project`）。结果不对就换名重试（如 `next.js` 而非 `nextjs`）。用户提到版本时用版本特定 ID
3. 用选定的 library ID + 用户完整问题（不要单字）调 `query-docs`
4. 用拉到的文档回答

## 1. 项目概述

**finget** 是一个基于 Python + [uv](https://github.com/astral-sh/uv) 管理的金融数据获取、存储与分发系统。

- **语言**: Python 3.11+
- **包管理**: uv + hatchling
- **存储引擎**: DuckDB（嵌入式列式 OLAP）
- **主要数据源**: tushare（可扩展）
- **当前版本**: 0.1.0
- **测试**: 166 个单元测试（8 个测试文件）
- **配置理念**: 极简 — 无 YAML 主配置文件，所有"基本不变"的参数 hardcode 进代码，唯一从外部读的是 `TUSHARE_TOKEN`（通过 `.env` 文件）；另有一个可选的 `config.yaml` 控制 `fetch latest` / `scan` 命令处理哪些数据集

## 2. 用户原始需求（7 项）

| # | 需求 | 对应实现 |
|---|------|---------|
| 1 | 数据获取层：HTTP/SDK 对接多数据源（tushare 等），获取 K线/指标/公司基础信息，配置外部化 | `fetchers/` + `config.py` |
| 2 | 持久化层：针对金融时序特点推荐存储方案（评估 DuckDB） | `storage/duckdb_store.py`，选型理由见 README |
| 3 | 数据更新策略：全量初始化 / 每日增量 / 遗漏扫描 / 自动查漏补缺 | `updater/strategies.py` (按 type 自动分流拉取行为) |
| 4 | 读取层：独立数据读取接口供下游调用 | `reader/data_reader.py` (`DataReader`) |
| 5 | 状态统计与进度：已下载/未下载量统计 + 实时进度展示 | `stats/collector.py` + `fetchers/progress.py` |
| 6 | 项目工程化：架构完整、高可维护、依赖文件完善、各层单元测试、考虑迁移与分发 | `pyproject.toml` + `tests/` + hatchling 构建 |

## 3. 项目结构

```
finget/
├── pyproject.toml              # 项目 & 依赖配置（hatchling 构建）
├── uv.lock                     # uv 锁文件
├── README.md                   # 项目文档 + DuckDB 选型对比表
├── AGENTS.md                   # 本文件
├── .env.example               # 环境变量配置模板（精简版：只含 TUSHARE_TOKEN + 3 个可选变量）
├── .env                        # 实际环境变量（gitignore，不提交）
├── .gitignore                  # 忽略 .env / data / __pycache__ 等
├── src/finget/
│   ├── __init__.py             # 版本号
│   ├── logging.py              # loguru 日志配置
│   ├── config.py               # Pydantic 配置模型（极简版：从 .env 加载）
│   ├── cli.py                  # Click CLI 入口（顶级命令 init/fetch/scan/stats/show + db 子组）
│   ├── fetchers/
│   │   ├── __init__.py
│   │   ├── base.py             # BaseFetcher 抽象基类（限速+分页）
│   │   ├── tushare_fetcher.py  # tushare 适配器
│   │   └── progress.py         # rich 进度条工具
│   ├── storage/
│   │   ├── __init__.py
│   │   └── duckdb_store.py     # DuckDB 存储引擎
│   ├── updater/
│   │   ├── __init__.py
│   │   └── strategies.py       # 更新策略（全量/增量/查漏补缺）
│   ├── reader/
│   │   ├── __init__.py
│   │   └── data_reader.py      # 下游读取接口
│   ├── stats/
│   │   ├── __init__.py
│   │   └── collector.py        # 统计收集器
└── tests/                      # 8 个测试文件，137 个用例
    ├── conftest.py             # 共享 fixtures（autouse 隔离 .env 加载路径）
    ├── test_config.py          # 配置层（环境变量加载、默认值、缺失 token 报错、策略文件）
    ├── test_storage.py         # 存储层（含幂等/文件型/去重/schema 鲁棒性）
    ├── test_fetchers.py        # 获取层（含 mock tushare + 测速 fallback + 并发拉取）
    ├── test_updater.py         # 更新策略（按 type 分流、日期范围参数、研报按天/按季、金股按月、表征一致性）
    ├── test_reader.py          # 读取层
    ├── test_stats.py           # 统计层
    └── test_cli.py             # CLI（init / init --schema-only / db recreate / fetch / fetch latest / scan / stats / show）
```

## 4. 核心接口速查

### 4.1 配置层 (`config.py`) — 极简版

**设计理念**：finget 不再使用 YAML 主配置文件。所有"基本不变"的参数 hardcode 在 Pydantic 模型默认值中，用户**无需关心**；唯一从外部读的是 `TUSHARE_TOKEN`（通过 `.env` 文件）。另有一个可选的 `config.yaml` 控制 `fetch latest` / `scan` 命令处理哪些数据集。

```python
from finget.config import get_config, Config, TushareConfig, StorageConfig, DEFAULT_DATASETS

cfg = get_config()  # 自动从 .env 加载，token 缺失时抛 ValueError
# cfg.fetcher.tushare.token         ← 从 .env 的 TUSHARE_TOKEN 读取
# cfg.storage.db_path               ← 默认 "data/finget.duckdb"（可被 FINGET_DB_PATH 覆盖）
# cfg.update.full_lookback_years    ← 默认 10（hardcode）
# cfg.datasets                      ← 默认 9 个数据集（hardcode，见 DEFAULT_DATASETS）
# cfg.strategy.latest_datasets      ← fetch latest 处理的数据集（默认 daily/adj_factor/daily_basic/stk_factor_pro）
# cfg.strategy.scan_datasets        ← scan 扫描的数据集（默认 daily/weekly/adj_factor/daily_basic）
```

**环境变量**：

| 环境变量 | 用途 | 默认值 |
|---------|------|--------|
| `TUSHARE_TOKEN` | **必填** tushare API token | 无（缺失即报错） |
| `TUSHARE_MIRROR_URLS` | 可选 镜像站列表（逗号分隔） | `https://fast.xiaodefa.cn,https://tt.xiaodefa.cn` |
| `FINGET_LOG_LEVEL` | 可选 日志级别 | `INFO` |
| `FINGET_DB_PATH` | 可选 DuckDB 文件路径 | `data/finget.duckdb` |
| `FINGET_CONFIG_FILE` | 可选 策略配置文件路径 | 项目根目录 `config.yaml` |

**策略配置文件** (`config.yaml`，可选)：
控制 `finget fetch latest` 和 `finget scan` 的行为，不传则使用内置默认值。查找路径：环境变量 `FINGET_CONFIG_FILE` → 项目根目录 → 当前工作目录。

**使用流程**：
1. `cp .env.example .env` → 在 `.env` 中填入 `TUSHARE_TOKEN=xxx`
2. （可选）`cp config.yaml.example config.yaml` 调整 latest/scan 数据集
3. 直接运行 `uv run finget init` — **无需** `-c config.yaml`！

**默认数据集列表**（`DEFAULT_DATASETS`，共 9 个）：

| name | type | api_name | daily_supported | params |
|------|------|----------|:---:|--------|
| stock_basic | stock_basic | stock_basic | — | `{list_status: L}` |
| daily | daily | daily | ✅ | `{}` |
| weekly | weekly | weekly | — | `{}` |
| adj_factor | adj_factor | adj_factor | ✅ | `{}` |
| daily_basic | daily_basic | daily_basic | ✅ | `{}` |
| trade_cal | trade_cal | trade_cal | — | `{exchange: SSE}` |
| report_rc | report_rc | report_rc | — | `{}` |
| stk_factor_pro | stk_factor_pro | stk_factor_pro | ✅ | `{}` |
| broker_recommend | broker_recommend | broker_recommend | — | `{}` |
| stk_surv | stk_surv | stk_surv | — | `{}` |
| hk_us_basic | hk_us_basic | hk_us_basic | — | `{}` |

### 4.2 获取层 (`fetchers/`)

```python
from finget.fetchers.base import BaseFetcher, FetchResult
from finget.fetchers.tushare_fetcher import TushareFetcher

fetcher = TushareFetcher(cfg.fetcher.tushare)
# 初始化时自动测速选择镜像站
fetcher.selected_url  # 当前选定的 API URL
# 单次获取（pro_api.query）
result: FetchResult = fetcher.fetch("daily", params={"ts_code": "000001.SZ"}, start_date="20240101")
# 分页全量
df = fetcher.fetch_all("daily", params={...}, page_size=5000)
# pro_bar（复权K线，自动传 api=pro 使用镜像站）
df = fetcher.pro_bar(ts_code="002594.SZ", start_date="20180101", adj="qfq")
# 股票列表
codes = fetcher.get_stock_list()
```

- `BaseFetcher`: 抽象基类，内置 `_throttle()` 限速 + `fetch_all()` 分页
- `FetchResult`: data(pd.DataFrame) + has_more + row_count
- `TushareFetcher`: 支持镜像站自动测速 + `pro_bar()` 封装 + `fetch_concurrent()` 并发拉取
- `BaseFetcher._throttle()`: 线程安全限速（`threading.Lock` 保护）
- 新增数据源：继承 `BaseFetcher`，实现 `fetch()` 方法

### 4.3 存储层 (`storage/duckdb_store.py`)

```python
from finget.storage import DuckDBStore

store = DuckDBStore(cfg.storage)
store.init_all(drop_existing=False)       # 初始化全部表；True 时 drop 后重建（用于 schema 升级）
store.init_table("daily")                 # 初始化单表
store.upsert("daily", df)                 # 幂等写入（ON CONFLICT 去重；自动适配 schema 差异）
store.query("SELECT * FROM daily")        # 返回 DataFrame
store.get_max_date("daily", "000001.SZ")  # 增量更新起点
store.get_max_date_col("trade_cal", "cal_date", where="exchange='SSE'")  # 自定义日期列
store.get_existing_dates(table, code, start, end)  # 查漏补缺
store.get_trade_dates(exchange="SSE", start=..., end=...)  # 获取交易日列表
store.get_cal_date_range("SSE")           # 某交易所日历的 (min_cal_date, max_cal_date)
store.get_table_columns("stock_basic")    # 读取表实际列名（用于 schema 差异处理）
store.count_rows("daily")
store.list_tables()
```

**关键设计**:
- 8 个预定义 schema: `stock_basic`, `trade_cal`, `daily`, `weekly`, `adj_factor`, `daily_basic`, `report_rc`, `stk_factor_pro`, `broker_recommend`
- `TIME_SERIES_DATASETS = {"daily", "weekly", "adj_factor", "daily_basic", "stk_factor_pro"}` — 时序表
- `CALENDAR_DATASETS = {"trade_cal"}` — 日历类数据
- `RESEARCH_DATASETS = {"report_rc"}` — 研报类（按季度并发拉取，5 列联合去重键）
- `BROKER_DATASETS = {"broker_recommend"}` — 券商金股类（按月并发拉取）
- UNIQUE 索引: stock_basic→`(ts_code)`, trade_cal→`(exchange, cal_date)`, report_rc→`(ts_code, report_date, org_name, author_name, quarter)`, broker_recommend→`(month, broker, ts_code)`, 时序表→`(ts_code, trade_date)`
- 日期列统一 `DATE` 类型，需在写入前转 `pd.to_datetime(...).dt.date`
- **`upsert()` schema 鲁棒性** (DuckDBStore.upsert):
  - DataFrame 多了表 schema 没有的列 → 警告 + 自动丢弃（适应 tushare API 字段升级）
  - DataFrame 缺了表 schema 有的列 → 用 `None` 填充（适应旧数据 / 子集字段）
  - 冲突键未在表里 → 抛 `ValueError` 快速失败
  - 写入流程: 列对齐 → 注册 `_tmp_upsert` 视图 → `INSERT ... ON CONFLICT DO UPDATE`

### 4.4 更新策略 (`updater/strategies.py`)

finget 不再使用"模式"概念。`UpdateStrategy.run()` 根据 `dataset.type` + 日期参数自动分流到对应的拉取行为，调用方无需关心内部调度。

```python
from finget.updater.strategies import UpdateStrategy

strategy = UpdateStrategy(fetcher, store, cfg)
# 行为由 dataset.type 自动决定，无需指定模式：
strategy.run(dataset)                                            # 增量：start_date=None 自动查 max_date 回溯
strategy.run(dataset, start_date="20240101", end_date="20241231")  # 指定日期范围
strategy.run_scan(dataset)                                       # 查漏补缺（独立方法）
# 也支持 date 对象：
from datetime import date
strategy.run(dataset, start_date=date(2024, 1, 1))
```

`start_date`/`end_date` 支持 `str` (`YYYYMMDD`/`YYYY-MM-DD`) 和 `date` 对象。

**行为分流规则**（`run()` 按 `dataset.type` 决定）:

| 数据集类型 | 行为 | 说明 |
|---|---|---|
| `stock_basic` | 一次性全量拉取 | `_update_stock_basic` |
| `hk_us_basic` | 港股+美股合并全量拉取 | `_update_hk_us_basic`，调 hk_basic+us_basic 两接口合并写入 |
| `trade_cal` | 按交易所一次性拉取 | `_update_calendar`，start_date=None 时查 max_date 回溯 |
| `report_rc` | 短跨度按天 / 长跨度按季度 | `_update_research`，≤60天逐日拉，>60天按季度分批 |
| `broker_recommend` | 按月份拉取 | `_update_broker`，start_date=None 时查最大 month 回溯 |
| `stk_surv` | 逐标的拉取 + 拆表写入 | `_update_survey`，content 拆 stk_surv_detail 表；start_date=None 时查 max(surv_date) 回溯 |
| `daily_supported` 时序表（无 ts_codes） | **按日全市场拉取** | `_update_by_date`，按 trade_date 单日拉全市场，5天并发合并写 |
| 其他时序表（weekly / 指定 ts_codes） | 逐标的拉取 | `_update_time_series`，start_date=None 时查 max_date 回溯，max_date=None 回溯 N 年 |

**时序表日期范围逻辑**（`_update_time_series` → `_fetch_one`，逐标的独立计算）:
- `start_date` 显式指定 → 用之
- `start_date` 为 None → 查该标的 `get_max_date` 回溯 `incremental_lookback_days` 天（增量）
- `max_date` 也为 None → 回溯 `full_lookback_years` 年（全量初始化）
- `end_date` 显式指定 → 用之；None → 今天

**按日全市场 vs 逐标的 性能对比**:

| 行为 | 接口调用数/日 | 速度 | 适用 |
|------|---------------|------|------|
| 逐标的拉取 | ~5400 次/日 | ~25 分钟 | 任意时序表（weekly / 指定 codes） |
| **按日全市场** | **1 次/日** | **~10 秒** | **daily/adj_factor/daily_basic/stk_factor_pro** |

按日全市场是日常运营的首选（速度提升 ~150 倍）。

**按日全市场提速优化** (v0.1.0+):
- `fetch_concurrent()`: 用 ThreadPoolExecutor(max_workers=3) 并发拉取多天数据
- 批量合并写入：攒 5 天 DataFrame 合并后一次 upsert（减少 DB 事务开销）
- `_throttle()` 加 `threading.Lock` 确保线程安全限速
- 实际提速约 2~3 倍（拉取部分并发化）、写入部分提速约 5 倍（事务数减少）
- API 限速（400/min）仍是根本瓶颈，并发只是减少线程间等待浪费

**report_rc 研报拉取策略**:
- 短跨度（≤ `RESEARCH_DAILY_THRESHOLD_DAYS`=60 天）：按天逐自然日拉取（规避镜像站大 offset 分页限制）
- 长跨度（> 60 天）：按季度分批并发拉取（每季约 2~4 万条，offset 不会太大）
- 每 3 个批次并发拉取（ThreadPoolExecutor max_workers=3），合并后一次 upsert 写入
- `_fetch_research_batch_df()`: 只返回 DataFrame（不写 DB），由上层合并写入；按天时传 (day, day) 单日区间
- report_rc 非交易日也有研报，按天逐自然日拉，不用交易日历过滤
- start_date=None 时查 max report_date 回溯；表空则回溯 `full_lookback_years` 年

**broker_recommend 券商金股拉取策略**:
- `_update_broker`: 每个月独立拉取（入参 `month=YYYYMM`），互不依赖
- 每 3 个月并发拉取（ThreadPoolExecutor max_workers=3），合并后一次 upsert 写入
- `_fetch_broker_month_df()`: 只返回 DataFrame（不写 DB），由上层合并写入
- start_date=None 时查最大 `month` 回溯；表空则回溯 `full_lookback_years` 年

### 4.5 读取层 (`reader/data_reader.py`)

```python
from finget.reader import DataReader

reader = DataReader(store)
reader.get_kline(ts_code="000001.SZ", start_date="20240101", end_date="20240131")
reader.get_close("000001.SZ")              # pd.Series
reader.get_stock_basic(industry="银行")
reader.get_stock_list()
reader.get_daily_basic(ts_code="000001.SZ")
reader.get_adj_factor(ts_code="000001.SZ")
reader.get_stk_factor(ts_code="000001.SZ")  # 技术面因子 MACD/KDJ/RSI/BOLL 等
reader.get_broker_recommend(month="202406") # 券商月度金股
reader.get_survey(ts_code="002223.SZ")           # 机构调研（不含 content，快）
reader.get_survey(ts_code="002223.SZ", with_content=True) # 机构调研（含 content 大文本）
reader.get_hk_us_basic(ts_code="00700.HK")       # 港美股基础信息
reader.raw_query("SELECT * FROM daily LIMIT 10")
```

### 4.6 统计层 (`stats/collector.py`)

```python
from finget.stats import StatsCollector

collector = StatsCollector(store)
collector.print_summary()                          # rich 表格输出
report_df = collector.missing_report("daily", ts_codes=[...])  # 缺失报告
```

### 4.7 CLI (`cli.py`)

```bash
# 一站式初始化（建表 + stock_basic + trade_cal，全量无需日期参数）
finget init
finget init --skip-stock-basic        # 只拉 trade_cal
finget init --skip-trade-cal           # 只拉 stock_basic
finget init --recreate                 # 删表重建（清空所有数据）

# 数据库管理
finget init --schema-only               # 仅建表结构（不拉数据，不需要 token）
finget db recreate --confirm           # 删表重建（需确认）

# 拉取/更新数据集（不传 -S 默认往前倒推一年）
finget fetch daily                     # 拉取近一年日线（按日全市场，极速）
finget fetch daily -S 20240101         # 从指定日期拉取日线
finget fetch report_rc -S 20210101     # 全量拉取研报
finget fetch broker_recommend -S 20160101  # 全量拉取金股

# 每日增量更新（按 config.yaml 配置）
finget fetch latest                    # 按策略配置更新指定数据集

# 查漏补缺（按 config.yaml 配置）
finget scan                            # 按策略配置扫描补齐缺失数据

# 数据统计与查看（顶级命令）
finget stats                           # 查看所有数据表统计
finget show daily -n 10                # 查看表内容
finget show daily -o output.csv        # 导出为 CSV
```

**命令层级**:

| 命令 | 作用 | 说明 |
|------|------|------|
| `finget init` | 一站式初始化 | 建表 → stock_basic → trade_cal（全量，无需日期） |
| `finget init --schema-only` | 仅建表 | 不拉数据，不需要 TUSHARE_TOKEN |
| `finget db recreate` | 删表重建 | schema 升级/完全重置 |
| `finget fetch <ds>` | 拉取数据 | 不传 -S 默认往前1年；daily_supported 自动按日全市场拉取 |
| `finget fetch latest` | 每日增量 | 按 `config.yaml` 配置更新 |
| `finget scan` | 查漏补缺 | 按 `config.yaml` 配置扫描 |
| `finget stats` | 数据统计 | 行数/标的数/日期范围/覆盖率 |
| `finget show <table>` | 查看表内容 | 快速查看或导出 |

**可用数据集**: daily, weekly, adj_factor, daily_basic, trade_cal, report_rc, stk_factor_pro, broker_recommend, stk_surv, hk_us_basic, stock_basic

**策略配置文件** (`config.yaml`，可选):

控制 `finget fetch latest` 和 `finget scan` 的行为。文件不存在时使用内置默认值。
查找路径：环境变量 `FINGET_CONFIG_FILE` → 项目根目录 → 当前目录。

```yaml
# config.yaml 示例
latest_datasets:        # fetch latest 更新的数据集
  - daily
  - adj_factor
  - daily_basic
  - stk_factor_pro

scan_datasets:          # scan 扫描的数据集
  - daily
  - weekly
  - adj_factor
  - daily_basic
```

> **注意**: finget 不再需要 `-c` 配置文件！所有配置从 `.env` + 代码默认值 + `config.yaml`(可选) 加载。

## 5. 数据库 Schema

| 表名 | 类型 | 去重键 | 说明 |
|------|------|--------|------|
| `stock_basic` | 基础 | `ts_code` | 股票基础信息（17 列：含 fullname / enname / cnspell / act_name / act_ent_type） |
| `trade_cal` | 日历 | `exchange, cal_date` | 交易日历（is_open/pretrade_date） |
| `daily` | 时序 | `ts_code, trade_date` | 日线行情 |
| `weekly` | 时序 | `ts_code, trade_date` | 周线行情 |
| `adj_factor` | 时序 | `ts_code, trade_date` | 复权因子 |
| `daily_basic` | 时序 | `ts_code, trade_date` | 每日指标(PE/PB等) |
| `report_rc` | 研报 | `ts_code, report_date, org_name, author_name, quarter` | 卖方盈利预测（22 列：含 op_rt/np/eps/pe/roe/rating/max_price/min_price） |
| `stk_factor_pro` | 时序 | `ts_code, trade_date` | 技术面因子（MACD/KDJ/RSI/BOLL/EMA/MA 等，约 200 列，含 bfq/hfq/qfq 三种复权） |
| `broker_recommend` | 券商金股 | `month, broker, ts_code` | 券商月度金股（4 列：month/broker/ts_code/name） |
| `stk_surv` | 机构调研 | `ts_code, surv_date, rece_org` | 机构调研主表（9 列元数据，不含 content 大文本） |
| `stk_surv_detail` | 机构调研详情 | `ts_code, surv_date, rece_org` | 调研内容详情表（content 大文本隔离，按需 JOIN） |
| `hk_us_basic` | 港美股基础 | `ts_code` | 港股+美股基础信息（3 列：ts_code/name/enname，合并 hk_basic+us_basic） |

**stock_basic 字段** (对齐 tushare pro.stock_basic 当前返回):
- `ts_code` (VARCHAR) — 股票代码
- `symbol` (VARCHAR) — 股票简称代码
- `name` (VARCHAR) — 股票名称
- `fullname` (VARCHAR) — 公司全名
- `enname` (VARCHAR) — 英文名称
- `cnspell` (VARCHAR) — 拼音缩写
- `area` (VARCHAR) — 所在地区
- `industry` (VARCHAR) — 所属行业
- `market` (VARCHAR) — 市场类别
- `exchange` (VARCHAR) — 交易所代码
- `curr_type` (VARCHAR) — 交易货币
- `list_status` (VARCHAR) — 上市状态 L/D/P
- `list_date` (DATE) — 上市日期
- `delist_date` (DATE) — 退市日期
- `is_hs` (VARCHAR) — 是否沪深港通标的
- `act_name` (VARCHAR) — 实控人名称
- `act_ent_type` (VARCHAR) — 实控人企业性质

**trade_cal 字段**:
- `exchange` (VARCHAR) — 交易所代码 (SSE/SZSE/CFFEX 等)
- `cal_date` (DATE) — 日历日期
- `is_open` (BOOLEAN) — 是否交易
- `pretrade_date` (DATE) — 上一交易日

**report_rc 字段** (卖方盈利预测):
- `ts_code` (VARCHAR) — 股票代码
- `name` (VARCHAR) — 股票名称
- `report_date` (DATE) — 研报日期
- `report_title` (VARCHAR) — 报告标题
- `report_type` (VARCHAR) — 报告类型
- `classify` (VARCHAR) — 报告分类
- `org_name` (VARCHAR) — 机构名称
- `author_name` (VARCHAR) — 作者
- `quarter` (VARCHAR) — 预测报告期（如 2024Q1）
- `op_rt` (DOUBLE) — 预测营业收入（万元）
- `op_pr` (DOUBLE) — 预测营业利润（万元）
- `tp` (DOUBLE) — 预测利润总额
- `np` (DOUBLE) — 预测净利润
- `eps` (DOUBLE) — 预测每股收益（元）
- `pe` (DOUBLE) — 预测市盈率
- `rd` (DOUBLE) — 预测股息率
- `roe` (DOUBLE) — 预测净资产收益率
- `ev_ebitda` (DOUBLE) — 预测 EV/EBITDA
- `rating` (VARCHAR) — 卖方评级（买入/增持/中性/减持等）
- `max_price` (DOUBLE) — 预测最高目标价
- `min_price` (DOUBLE) — 预测最低目标价
- `imp_dg` (VARCHAR) — 机构关注度
- `create_time` (TIMESTAMP) — TS 数据更新时间

> **report_rc 用法**:
> - tushare 文档: https://tushare.pro/document/2?doc_id=298
> - 入参: `ts_code` / `report_date` / `start_date` / `end_date`（都可选）
> - **不支持按日全市场查询**（无 `daily_supported`），只能按日期区间批量拉
> - **镜像站不支持大 offset 分页**（offset 超过约 100000 会报"查询数据失败"），因此采用**按季度分批并发拉取**策略（`_update_research` → `_generate_quarters` + `_fetch_research_batch_df` + ThreadPoolExecutor 3线程并发）
> - 建议下载方式: `uv run finget fetch report_rc -S 20210101 -E 20261231`（按季度并发分批，每季约 2~4 万行，3 季度合并写入）

> **broker_recommend 用法**:
> - tushare 文档: https://tushare.pro/document/2?doc_id=298
> - 入参: `month`（必选，YYYYMM 格式，如 202106）
> - 单次最大 1000 条，按月并发拉取（ThreadPoolExecutor 3线程）
> - 建议下载方式: `uv run finget fetch broker_recommend -S 20160101 -E 20260630`（按月并发分批，3 月合并写入）

## 6. 依赖

**核心**: duckdb, tushare, pandas, numpy, pydantic, pydantic-settings, pyyaml, click, rich, httpx, loguru

**dev**: pytest, pytest-cov, pytest-asyncio, ruff, mypy, respx

## 7. 已知限制 & 待改进

1. **交易日历**: `SCAN` 模式依赖 tushare `trade_cal` 接口，非 tushare 数据源无法自动查漏
2. **DuckDB 并发**: 单写入者模型，不支持多进程并发写入同一文件
3. **数据源扩展**: 目前仅实现 tushare，新增数据源需继承 `BaseFetcher`
4. **增量更新容错**: 当前回溯 `incremental_lookback_days` 天，对停牌/节假日可能冗余拉取

## 8. 开发约定

- 日志: 使用 `from finget.logging import log`
- 测试: 内存 DB fixture (`tmp_config` + `store`)，不触碰磁盘
- 配置: 新增配置项需在 `config.py` 的 Pydantic 模型中声明
- 新增数据集: 在 `SCHEMAS` dict 中添加 DDL，时序表加入 `TIME_SERIES_DATASETS`
- 代码风格: ruff (line-length=100, select E/F/I/N/W/UP/B/SIM/TCH)

## 9. 架构要点（跨文件理解）

finget 采用分层架构，数据流方向为：**数据源 → Fetcher → Updater → Storage → Reader/Stats**。每一层只依赖下一层的抽象接口，不反向依赖。

### 9.1 分层依赖关系

```
CLI (cli.py)
 ├─→ Updater (strategies.py) ── 协调器，组合 Fetcher + Store + Config
 │     ├─→ Fetcher (base.py / tushare_fetcher.py) ── 数据获取 + 限速 + 分页
 │     ├─→ Store (duckdb_store.py) ── 幂等写入 + 时序查询
 │     └─→ Progress (progress.py) ── rich 进度条
 ├─→ Reader (data_reader.py) ── 只读访问 Store，面向下游
 └─→ Stats (collector.py) ── 只读访问 Store，生成统计报告
```

**关键依赖原则**:
- `Updater` 是唯一同时持有 `Fetcher` 和 `Store` 引用的模块，是"写路径"的协调器
- `Reader` 和 `Stats` 只读 `Store`，不接触 `Fetcher`，保证读路径无副作用

### 9.2 数据流转中的日期格式陷阱

tushare API 使用 `YYYYMMDD` 字符串日期（如 `"20240101"`），但 DuckDB 存储层统一使用 `DATE` 类型。这个转换发生在 `Updater` 层：

- `_update_stock_basic()`: `pd.to_datetime(df["list_date"], format="%Y%m%d").dt.date`
- `_fetch_one()`: `pd.to_datetime(df["trade_date"], format="%Y%m%d").dt.date`

**写入 Store 前必须完成此转换**，否则 DuckDB 会报类型错误。`DataReader` 对外接受 `str | date`，内部用 `_to_date()` 归一化。

### 9.3 幂等写入机制

`DuckDBStore.upsert()` 基于 DuckDB 的 `INSERT ... ON CONFLICT DO UPDATE` 语义实现幂等写入：
- 冲突键自动推断: `stock_basic` → `[ts_code]`，时序表 → `[ts_code, trade_date]`
- 依赖 `init_table()` 时创建的 UNIQUE 索引（`uidx_<table>_<cols>`）
- 使用临时视图 `_tmp_upsert` 注册 DataFrame，避免参数化批量插入的复杂性
- **schema 差异自适应** (v0.1.0+): 写入前先把 DataFrame 与表 schema 对齐
  - DataFrame 多余列 → log.warning + drop（适应 tushare API 字段新增）
  - DataFrame 缺失列 → 填 `None`（适应子集字段或旧 API 返回）
  - 冲突键未在表里 → 抛 `ValueError` 快速失败
  - 历史教训：tushare pro.stock_basic 在 2024 年后增加了 `fullname`/`enname`/`cnspell`/`act_name`/`act_ent_type` 字段，老 schema 会触发 `Binder Error: Referenced update column xxx not found in table`，鲁棒化后这类问题自动修复但建议用 `init --recreate` 升级

**重要**: 若 UNIQUE 索引未创建，`ON CONFLICT` 不会生效，会变成普通 INSERT 导致重复数据。

### 9.4 更新策略的行为分流

finget 不再使用 `UpdateMode` 模式枚举。`UpdateStrategy.run()` 根据数据集**类型** + 日期参数自动分流到对应的拉取行为：

| 类型分流 | 数据集示例 | 处理方式 |
|---------|-----------|---------|
| 基础信息 (`stock_basic`) | `stock_basic` | 全量一次拉取 → 整体 upsert |
| 港美股基础 (`HK_US_BASIC_DATASETS`) | `hk_us_basic` | hk_basic+us_basic 两接口合并 → 整体 upsert |
| 日历类 (`CALENDAR_DATASETS`) | `trade_cal` | 整体一次拉取 → 幂等 upsert（不分 ts_code） |
| 研报类 (`RESEARCH_DATASETS`) | `report_rc` | 短跨度按天 / 长跨度按季度分批并发拉取 → 合并 upsert |
| 券商金股类 (`BROKER_DATASETS`) | `broker_recommend` | 按月份分批并发拉取 → 合并 upsert |
| 机构调研类 (`SURVEY_DATASETS`) | `stk_surv` | 逐标的拉取 → 主表+detail 表分别 upsert |
| `daily_supported` 时序表（无 ts_codes） | `daily`/`adj_factor`/`daily_basic`/`stk_factor_pro` | **按 trade_date 单日全市场拉取** → 幂等 upsert |
| 其他时序表 | `weekly` / 指定 ts_codes | 逐标的拉取 → 幂等 upsert |

各行为日期范围逻辑：

- **按日全市场**（`_update_by_date`）：按 `trade_date` 单日拉全市场（一次接口拿 5000+ 行）；仅 `daily_supported=True` 的数据集；不指定日期时自动从 `max_date+1` 到今天
- **逐标的拉取**（`_update_time_series` → `_fetch_one`）：对每个标的，`start_date` 显式指定则用之；None 时查 `get_max_date` 回溯 `incremental_lookback_days` 天（增量）；`max_date` 也为 None 则回溯 `full_lookback_years` 年（全量初始化）
- **日历类**：`start_date` None 时查 `get_max_date_col("cal_date")` 回溯
- **研报类**：`start_date` None 时查 `report_date` 回溯；表空则回溯 N 年
- **金股类**：`start_date` None 时查最大 `month` 回溯；表空则回溯 N 年
- **查漏补缺**（`run_scan`，独立方法）：对比交易日历与已有日期，找出缺失，分批（`scan_batch_size`）补齐；**仅对时序表生效**

**查漏补缺的隐含依赖**：先有 `trade_cal` 表数据。`_get_trade_calendar()` 优先从 `trade_cal` 表读（需要先执行 `fetch trade_cal`），表不存在时回退到直接调用 tushare。scan 还要求标的已有数据（`min_date`/`max_date` 非 None），完全空表的标的需要先拉取初始化数据。

### 9.5 配置驱动的数据集发现

`Config.datasets` 是 `list[DatasetConfig]`，每个数据集通过 `name`（表名）与 `type`（schema 类型）关联。`type` 必须存在于 `SCHEMAS` dict 中。`Updater` 通过 `ds.name` 在 `config.datasets` 中查找配置，因此 **YAML 中的 `name` 必须与期望的表名一致**。

### 9.6 镜像站自动测速机制

`TushareFetcher` 支持通过 `mirror_urls` 配置多个 tushare 镜像站地址，初始化时自动测速选择最快的一个。这是一个跨配置+获取层的设计：

**配置层** (`TushareConfig`):
- `mirror_urls: list[str]` — 镜像站地址列表，默认 `["https://fast.xiaodefa.cn", "https://tt.xiaodefa.cn"]`
- `base_url: str` — 单一 API 地址，仅当 `mirror_urls` 为空时使用
- `speed_test_timeout: float` — 测速超时秒数，默认 5.0
- **优先级**: `mirror_urls` > `base_url`

**测速逻辑** (`TushareFetcher._select_best_url()`):
1. 若 `mirror_urls` 为空，直接使用 `base_url`
2. 否则对每个 URL 发送 HTTP HEAD 请求测速
3. 优先 HTTPS，SSL 错误时自动降级 HTTP 重试
4. 选响应时间最短的成功 URL
5. 全部失败则 fallback 到 `base_url`

**URL 应用**（关键步骤）:
- `pro._DataApi__http_url = selected_url` — 通过 Python name mangling 设置 pro_api 的内部 URL
- `ts.pro_bar()` 等模块级函数必须传 `api=pro` 参数才能使用镜像站，否则走默认官方地址

**陷阱**: `ts.pro_bar()` 是 tushare 模块级函数，**不会继承** pro_api 实例的 URL 设置。必须显式传 `api=pro`:
```python
df = ts.pro_bar(ts_code='002594.SZ', api=pro, start_date='20180101', adj='qfq')
```
`TushareFetcher.pro_bar()` 方法已封装此逻辑，始终传 `api=self._api`。

## 10. 常用开发命令

> 项目使用 [uv](https://github.com/astral-sh/uv) 管理，所有命令前缀 `uv run`。

### 10.1 环境安装

```bash
uv sync --extra dev    # 安装全部依赖（含 dev）
```

### 10.2 运行测试

```bash
uv run pytest                          # 全部测试
uv run pytest tests/test_storage.py    # 单个测试文件
uv run pytest tests/test_storage.py::test_upsert_basic  # 单个用例
uv run pytest -k "upsert"              # 按名称匹配
uv run pytest --cov=finget             # 带覆盖率
uv run pytest -m "not slow"            # 排除 slow 标记
```

### 10.3 代码检查

```bash
uv run ruff check src/ tests/          # lint
uv run ruff check --fix src/           # 自动修复
uv run ruff format src/                # 格式化
uv run mypy src/finget                 # 类型检查（strict 模式；当前有 6 个历史遗留错误，CI 会暴露）
```

**CI** (`.github/workflows/ci.yml`): 4 个 job 在 push/PR 时自动跑

| Job | 内容 | 触发 |
|-----|------|------|
| `test (3.11/3.12)` | pytest + coverage | 每次 push/PR |
| `lint` | ruff check + format check | 每次 push/PR |
| `typecheck` | mypy strict | 每次 push/PR |
| `build` | uv build (wheel + sdist) | 每次 push/PR |

Dependabot (`.github/dependabot.yml`): 每周一 09:00 自动检测 uv 依赖 + GitHub Actions 版本更新并开 PR。

### 10.4 构建 & 安装

```bash
uv build                               # 构建 wheel + sdist
uv pip install -e .                    # 开发模式安装（editable）
```

### 10.5 CLI 使用

> **极简模式** — finget 不再需要 `-c` 配置文件！所有命令从 `.env` 读取 token，策略配置从 `config.yaml`(可选) 读取。

```bash
# 无需 -c！直接运行（前提：.env 中有 TUSHARE_TOKEN）
uv run finget init              # 一站式初始化（建表+stock_basic+trade_cal）
uv run finget init --schema-only # 仅建表（不含数据，不需要 token）
uv run finget fetch daily       # 拉取近一年日线（按日全市场，极速）
uv run finget fetch latest      # 按策略配置每日增量更新
uv run finget scan              # 按策略配置查漏补缺
uv run finget stats             # 数据统计
uv run finget show daily -n 10 -o out.csv
```

**`init` 命令** — 一站式数据库初始化（推荐首次运行使用）：

```bash
# 默认：建表 + 拉全部上市股票（list_status=L） + 拉 SSE+SZSE 全部交易日历
uv run finget init

# 自定义交易所（如只拉上交所）
uv run finget init --exchanges SSE

# 跳过某项
uv run finget init --skip-trade-cal  # 只建表+拉股票
uv run finget init --skip-stock-basic # 只建表+拉日历

# 拉退市股票（D=退市, P=暂停上市, L=上市，默认 L）
uv run finget init --list-status D

# 升级 schema（tushare 字段变化时使用，会清空所有数据！）
uv run finget init --recreate
```

`init` 流程：
1. `init_all(drop_existing=recreate)` 创建所有表（`--recreate` 时先 DROP 全部已存在表）
2. 拉取 `stock_basic`（带 rich 进度条，按 list_status 过滤）
3. 按交易所分别拉取 `trade_cal`（带 rich 进度条，每个交易所一阶段，全量拉取）
4. 显示汇总表格：股票数 / 各交易所日历范围 / 总耗时

**fetch 命令的日期参数**（适用于所有数据集类型：stock_basic / trade_cal / 时序表）：

```bash
# 拉取近一年日线（不传 -S 默认往前倒推一年，daily_supported 自动按日全市场拉取）
uv run finget fetch daily

# 从指定日期拉取日线
uv run finget fetch daily -S 20240101 -E 20241231

# 拉取指定日期范围的交易日历
uv run finget fetch trade_cal -S 20240101 -E 20240131

# 拉取股票基础信息（一次性全量，无需日期）
uv run finget fetch stock_basic
```

- `--start-date` / `-S`: 起始日期（YYYYMMDD 或 YYYY-MM-DD），不传则默认往前倒推一年（365 天）
- `--end-date` / `-E`: 结束日期（YYYYMMDD 或 YYYY-MM-DD），不传则到今天
- stock_basic / trade_cal 不需要日期参数
- 行为自动决定：daily_supported 数据集按日全市场拉取（极速）；其他逐标的拉取

**fetch latest 命令**（每日增量，按策略配置）：

```bash
# 按 config.yaml 中 latest_datasets 配置做每日增量更新
uv run finget fetch latest
```

**scan 命令**（查漏补缺，按策略配置）：

```bash
# 按 config.yaml 中 scan_datasets 配置扫描补齐缺失数据
uv run finget scan
```

**show 命令**（预览表内容）：

```bash
uv run finget show daily -n 10              # 打印前 10 行到终端
uv run finget show daily -n 100 -o out.csv  # 导出前 100 行到 CSV
```

**stats 命令**：

```bash
uv run finget stats                         # 各表行数 / 时间范围
```

## 11. 变更日志

已迁移到独立文件 [`CHANGELOG.md`](./CHANGELOG.md)。
