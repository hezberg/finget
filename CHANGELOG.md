# CHANGELOG

> 详细代码变更查 `git log`；本文件记录功能性变更和影响。

## 2026-06-24 — 新增 report_rc（卖方盈利预测）

- 加 `report_rc` 数据集：22 列 schema，5 列联合唯一索引 `(ts_code, report_date, org_name, author_name, quarter)`
- 新增 `_update_research()` 方法（按 start_date/end_date 区间批量拉，page_size=3000）
- 新增 `RESEARCH_DATASETS = {"report_rc"}` 常量 + `UpdateStrategy.run()` 路由分支
- 踩坑：tushare report_rc 单次最大 3000 条（其他接口 5000），超了报"查询数据失败，请确认参数"（误导性错误，看着像 token 问题）
- 用法：`uv run finget fetch report_rc -m full -S 20210101 -E 20251231`

## 2026-06-24 — rate_limit 200 → 400

- `TushareConfig.rate_limit_per_min` 默认从 200 提到 400
- 全量 daily 从 25-30 min 缩到 12-15 min

## 2026-06-24 — GitHub Actions CI

- `.github/workflows/ci.yml`: 4 个 job（test×2 版本 / lint / typecheck / build）
- `.github/dependabot.yml`: uv + GitHub Actions 每周自动检测依赖更新
- 触发：push 到 main / PR 到 main

## 2026-06-24 — DAILY 模式（按 trade_date 单日全市场拉取）

- `UpdateMode.DAILY` 新模式 + `_update_by_date()` 方法
- `DatasetConfig.daily_supported: bool` 字段（仅 daily/adj_factor/daily_basic 是 True）
- CLI `-m` 选项新增 `daily` 值
- 速度对比：`adj_factor -m daily` ~10 秒（vs INCREMENTAL 的 25 分钟，提升 150 倍）

## 2026-06-24 — 移除 deploy 模块（极简化）

- 删除 `src/finget/deploy/` + `tests/test_deploy.py` + `config/` 目录
- 删除 `pyproject.toml` 的 paramiko 依赖
- CLI 从 8 命令减为 6 命令（删除 deploy / remote）
- 测试 123 → 114

## 2026-06-24 — trade_cal + 日期范围

- 新增 `trade_cal` 表 + `CALENDAR_DATASETS` 常量
- `_update_calendar()` 方法处理日历类数据集
- `UpdateStrategy.run()` 支持 `start_date`/`end_date` 参数（str / date 对象）
- CLI `fetch` 加 `-S` / `-E` 选项

## 2026-06-24 — 一站式 init CLI

- `finget init` 命令：建表 + 拉 stock_basic + 拉 trade_cal
- 选项：`--exchanges` / `-S/-E` / `--skip-stock-basic` / `--skip-trade-cal` / `--list-status` / `--recreate`
- rich Progress 进度条 + 完成后汇总表格

## 2026-06-24 — 配置极简化

- 删除 YAML 配置，所有参数 hardcode 到 Pydantic 模型默认值
- 唯一从外部读 `TUSHARE_TOKEN`（通过 .env）
- `get_config()` 用 `lru_cache` + `_build_config_from_env()`
- 上手只需 `cp .env.example .env` + 填 token + `uv run finget init`

## 2026-06-24 — schema 鲁棒化 + init 升级

- stock_basic 扩到 17 列（新增 fullname/enname/cnspell/act_name/act_ent_type 等）
- `upsert()` 增加 schema 差异自适应：多余列 drop + 缺失列填 None
- `init_all(drop_existing=False)` + CLI `--recreate` flag 用于 schema 升级
- 历史教训：tushare 字段升级会触发 Binder Error，需要用 `init --recreate` 重建
