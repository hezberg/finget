"""finget CLI 入口.

命令层级:
  finget init               一站式初始化（建表 + stock_basic + trade_cal）
  finget init --schema-only 仅建表结构（不拉数据，不需要 token）
  finget fetch <ds> -S -E   拉取数据集（不传日期默认往前倒推一年）
  finget fetch latest       按策略配置文件做每日增量更新
  finget scan               按策略配置文件做查漏补缺
  finget stats              数据统计
  finget show <table>       查看表内容
  finget db recreate        删表重建
"""

from __future__ import annotations

import os
import sys
import time
from datetime import date, datetime
from pathlib import Path

import click
import pandas as pd
from rich.console import Console
from rich.table import Table

from finget.config import Config
from finget.logging import log, setup_logging

console = Console()


# ---------------------------------------------------------------------------
# 公共工具函数
# ---------------------------------------------------------------------------

def _load_config() -> tuple[Config, str]:
    """加载配置（从 .env / 环境变量 / config.yaml）."""
    from finget.config import load_config

    cfg = load_config()
    setup_logging(level=cfg.log_level)
    return cfg, "(default: from .env)"


def _parse_date(s: str | None, name: str) -> str | None:
    """解析并校验 YYYYMMDD / YYYY-MM-DD 格式日期."""
    if s is None:
        return None
    s_norm = s.replace("-", "")
    try:
        datetime.strptime(s_norm, "%Y%m%d")
    except ValueError:
        console.print(f"[red]Invalid {name} '{s}': expected YYYYMMDD or YYYY-MM-DD[/red]")
        sys.exit(1)
    return s_norm


def _get_cfg_or_exit() -> Config:
    """加载配置，失败时打印友好提示并退出."""
    try:
        cfg, _ = _load_config()
        return cfg
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

AVAILABLE_DATASETS = [
    "stock_basic", "daily", "weekly", "adj_factor", "daily_basic",
    "trade_cal", "report_rc", "stk_factor_pro", "broker_recommend", "stk_surv",
    "hk_us_basic",
]

DATASET_DESCRIPTIONS = {
    "stock_basic": "股票基础信息（一次性全量拉取）",
    "daily": "日线行情（支持按日全市场拉取，极速）",
    "weekly": "周线行情",
    "adj_factor": "复权因子（支持按日全市场拉取）",
    "daily_basic": "每日指标 PE/PB/换手率等（支持按日全市场拉取）",
    "trade_cal": "交易日历",
    "report_rc": "卖方研报盈利预测（按季度并发拉取）",
    "stk_factor_pro": "技术面因子 MACD/KDJ/RSI/BOLL 等（支持按日全市场拉取）",
    "broker_recommend": "券商月度金股（按月并发拉取）",
    "stk_surv": "机构调研记录（逐标的拉取，content 拆详情表）",
    "hk_us_basic": "港美股基础信息（港股+美股合并，一次性全量）",
}


@click.group()
def main() -> None:
    """finget — 金融数据获取工具.

    快速上手:
      finget init                # 一站式初始化（建表 + 基础数据）
      finget fetch daily         # 拉取日线行情（默认往前1年）
      finget fetch latest        # 每日增量更新（按策略配置）
      finget stats               # 查看数据覆盖情况

    可用数据集: daily, weekly, adj_factor, daily_basic, trade_cal,
                report_rc, stk_factor_pro, broker_recommend, stk_surv, hk_us_basic, stock_basic
    """


# =========================================================================
# init — 一站式初始化
# =========================================================================

@main.command()
@click.option(
    "--exchanges",
    "-e",
    default="SSE,SZSE",
    help="交易日历交易所（逗号分隔），默认 SSE,SZSE",
)
@click.option(
    "--skip-stock-basic",
    is_flag=True,
    default=False,
    help="跳过 stock_basic 拉取",
)
@click.option(
    "--skip-trade-cal",
    is_flag=True,
    default=False,
    help="跳过 trade_cal 拉取",
)
@click.option(
    "--list-status",
    default="L",
    show_default=True,
    help="股票状态 L=上市 D=退市 P=暂停",
)
@click.option(
    "--recreate",
    is_flag=True,
    default=False,
    help="删除已有表再重建（会清空所有数据！）",
)
@click.option(
    "--schema-only",
    is_flag=True,
    default=False,
    help="仅建表结构，不拉取数据（等价于原 db init，不需要 TUSHARE_TOKEN）",
)
def init(
    exchanges: str,
    skip_stock_basic: bool,
    skip_trade_cal: bool,
    list_status: str,
    recreate: bool,
    schema_only: bool,
) -> None:
    """一站式初始化数据库.

    依次执行: 建表 → 拉取 stock_basic → 拉取 trade_cal
    完成后显示汇总表格。

    --schema-only: 仅建表结构，不拉取数据，不需要 TUSHARE_TOKEN。
    """
    from finget.storage.duckdb_store import DuckDBStore

    # --schema-only: 只建表，跳过所有拉取，不需要 token
    # 用轻量配置（只读 db_path），绕过 load_config 的 token 强制校验
    if schema_only:
        from finget.config import StorageConfig, _PROJECT_ROOT
        db_path = os.environ.get("FINGET_DB_PATH", "data/finget.duckdb")
        # 相对路径基于项目根目录解析（与 config.py 一致）
        if not os.path.isabs(db_path):
            db_path = str(_PROJECT_ROOT / db_path)
        store = DuckDBStore(StorageConfig(db_path=db_path))
        if recreate:
            console.print("[yellow]⚠ --recreate 已开启, 将删除已存在的表再重建[/yellow]")
        store.init_all(drop_existing=recreate)
        tables = store.list_tables()
        store.close()
        console.print(f"[green]✓ 已创建 {len(tables)} 张表: {', '.join(tables)}[/green]")
        console.print("[dim]提示: 数据表已创建但为空，请用 [bold]finget fetch[/bold] 拉取数据[/dim]")
        return

    cfg = _get_cfg_or_exit()

    if skip_stock_basic and skip_trade_cal:
        console.print("[red]不能同时跳过 stock_basic 和 trade_cal（如只需建表请用 --schema-only）[/red]")
        sys.exit(1)

    from finget.fetchers.tushare_fetcher import TushareFetcher
    from finget.updater.strategies import UpdateStrategy
    from finget.fetchers.progress import create_progress

    if cfg.fetcher.tushare is None:
        console.print("[red]No tushare config found.[/red]")
        sys.exit(1)

    fetcher = TushareFetcher(cfg.fetcher.tushare)
    store = DuckDBStore(cfg.storage)
    strategy = UpdateStrategy(fetcher, store, cfg)
    exchange_list = [e.strip() for e in exchanges.split(",") if e.strip()]

    started_at = time.monotonic()

    # ---------- 1. 初始化所有表结构 ----------
    console.print("[bold cyan]▶ Step 1/3: 初始化表结构[/bold cyan]")
    if recreate:
        console.print("  [yellow]⚠ --recreate 已开启, 将删除已存在的表再重建[/yellow]")
    store.init_all(drop_existing=recreate)
    tables = store.list_tables()
    console.print(f"  [green]✓[/green] 已创建 {len(tables)} 张表: {', '.join(tables)}")

    # ---------- 2. 拉取 stock_basic ----------
    sb_rows = 0
    sb_started = time.monotonic()
    if not skip_stock_basic:
        console.print("\n[bold cyan]▶ Step 2/3: 拉取股票基础信息 (stock_basic)[/bold cyan]")
        ds = next(
            (d for d in cfg.datasets if d.type == "stock_basic"),
            None,
        )
        if ds is None:
            from finget.config import DatasetConfig
            ds = DatasetConfig(
                name="stock_basic", type="stock_basic", api_name="stock_basic",
                params={"list_status": list_status},
            )
        else:
            ds = ds.model_copy(update={"params": {**ds.params, "list_status": list_status}})

        with create_progress() as progress:
            task = progress.add_task("[cyan]拉取 stock_basic...[/cyan]", total=None)
            try:
                sb_rows = strategy.run(ds)
                progress.update(task, completed=1, total=1)
            except Exception as e:
                progress.stop()
                log.error(f"stock_basic 拉取失败: {e}")
                console.print(f"  [red]✗ 失败: {e}[/red]")
                sys.exit(1)
        sb_elapsed = time.monotonic() - sb_started
        console.print(
            f"  [green]✓[/green] stock_basic: {sb_rows:,} 只股票"
            f"（list_status={list_status}） 耗时 {sb_elapsed:.1f}s"
        )
    else:
        console.print("\n[bold cyan]▶ Step 2/3: stock_basic 跳过[/bold cyan]")

    # ---------- 3. 拉取 trade_cal ----------
    tc_summary: list[tuple[str, int, str | None, str | None]] = []
    tc_total_rows = 0
    if not skip_trade_cal:
        console.print("\n[bold cyan]▶ Step 3/3: 拉取交易日历 (trade_cal)[/bold cyan]")

        from finget.config import DatasetConfig
        with create_progress() as progress:
            overall = progress.add_task("[cyan]总进度[/cyan]", total=len(exchange_list))
            for ex in exchange_list:
                ex_task = progress.add_task(f"[cyan]{ex}[/cyan]", total=None)
                ds = DatasetConfig(
                    name="trade_cal", type="trade_cal", api_name="trade_cal",
                    params={"exchange": ex},
                )
                try:
                    rows = strategy.run(ds)
                except Exception as e:
                    log.error(f"trade_cal {ex} 拉取失败: {e}")
                    progress.update(ex_task, completed=1, total=1)
                    progress.advance(overall)
                    continue

                min_d, max_d = store.get_cal_date_range(ex)
                tc_summary.append((ex, rows, str(min_d) if min_d else None, str(max_d) if max_d else None))
                tc_total_rows += rows
                progress.update(ex_task, completed=1, total=1)
                progress.advance(overall)
    else:
        console.print("\n[bold cyan]▶ Step 3/3: trade_cal 跳过[/bold cyan]")

    total_elapsed = time.monotonic() - started_at

    # ---------- 汇总表格 ----------
    console.print()
    summary = Table(title="基础数据初始化结果", show_lines=True, title_style="bold green")
    summary.add_column("数据集", style="cyan", no_wrap=True)
    summary.add_column("条目数", justify="right", style="green")
    summary.add_column("交易所/筛选", style="yellow")
    summary.add_column("日期范围", style="magenta")

    if not skip_stock_basic:
        summary.add_row("stock_basic", f"{sb_rows:,}", f"list_status={list_status}", "-")
    for tc_entry in tc_summary:
        ex_name, ex_rows, cal_min, cal_max = tc_entry
        date_range = f"{cal_min or '-'} ~ {cal_max or '-'}"
        summary.add_row("trade_cal", f"{ex_rows:,}", ex_name, date_range)
    console.print(summary)

    store.close()

    console.print(f"\n[bold green]✓ 初始化完成[/bold green] 总耗时 [cyan]{total_elapsed:.1f}s[/cyan]")
    if not skip_stock_basic and not skip_trade_cal:
        console.print(
            "[dim]提示: 接下来可执行 [bold]finget fetch daily[/bold] 拉取日线行情[/dim]"
        )


# =========================================================================
# db 子组 — 数据库操作
# =========================================================================

@main.group()
def db() -> None:
    """数据库管理操作.

    子命令:
      recreate  删表重建（清空所有数据后重建表结构）

    注: 仅建表请用 `finget init --schema-only`。
    """


@db.command()
@click.option(
    "--confirm",
    is_flag=True,
    default=False,
    help="确认删除所有表并重建（不传此参数会先提示确认）",
)
def recreate(confirm: bool) -> None:
    """删除所有表并重建（会清空全部数据！）.

    用于 schema 升级或完全重置场景。
    如果不传 --confirm，会先打印警告并等待确认。
    """
    cfg = _get_cfg_or_exit()
    from finget.storage.duckdb_store import DuckDBStore

    if not confirm:
        store = DuckDBStore(cfg.storage)
        tables = store.list_tables()
        store.close()
        if tables:
            console.print(
                f"[yellow]⚠ 将删除以下 {len(tables)} 张表及其全部数据:[/yellow]\n"
                f"  {', '.join(tables)}"
            )
            console.print("[yellow]请加上 --confirm 参数确认操作[/yellow]")
            sys.exit(1)

    store = DuckDBStore(cfg.storage)
    store.init_all(drop_existing=True)
    tables = store.list_tables()
    store.close()
    console.print(f"[green]✓ 已重建 {len(tables)} 张表[/green]")
    console.print("[dim]提示: 所有数据已清空，请用 [bold]finget init[/bold] 重新初始化[/dim]")


# =========================================================================
# fetch — 拉取/更新数据集
# =========================================================================

@main.command()
@click.argument("dataset_name")
@click.option("--codes", "-s", default=None, help="指定标的代码（逗号分隔，如 000001.SZ,600000.SH）")
@click.option(
    "--start-date",
    "-S",
    default=None,
    help="起始日期 YYYYMMDD（不传则自动增量：表有数据从 max_date+1 开始，空表回溯几天；首次拉历史请显式指定）",
)
@click.option(
    "--end-date",
    "-E",
    default=None,
    help="结束日期 YYYYMMDD（不传则到今天）",
)
def fetch(
    dataset_name: str,
    codes: str | None,
    start_date: str | None,
    end_date: str | None,
) -> None:
    """拉取或更新数据集.

    DATASET_NAME 是要拉取的数据集名称，可用:
      stock_basic  股票基础信息（一次性全量）
      daily        日线行情 ★
      weekly       周线行情
      adj_factor   复权因子 ★
      daily_basic  每日指标 ★
      trade_cal    交易日历
      report_rc    卖方研报盈利预测
      stk_factor_pro 技术面因子 ★
      broker_recommend 券商月度金股
      latest       特殊值：按策略配置文件做每日增量更新

    ★ 标记的数据集支持按日全市场拉取（速度极快）

    不传 --start-date 时自动增量：表有数据从 max_date+1 开始，空表回溯几天。
    首次拉取历史数据请用 -S 指定起点（如 -S 20200101）。

    常用示例:
      finget fetch daily                     # 自动增量（从上次 max_date+1 到今天）
      finget fetch daily -S 20240101         # 从指定日期拉取日线
      finget fetch report_rc -S 20210101     # 全量拉取研报
      finget fetch broker_recommend -S 20160101  # 全量拉取金股
      finget fetch latest                    # 按策略配置每日增量更新
    """
    cfg = _get_cfg_or_exit()

    from finget.fetchers.tushare_fetcher import TushareFetcher
    from finget.storage.duckdb_store import DuckDBStore
    from finget.updater.strategies import UpdateStrategy

    if cfg.fetcher.tushare is None:
        console.print("[red]No tushare config found.[/red]")
        sys.exit(1)

    start_date = _parse_date(start_date, "start_date")
    end_date = _parse_date(end_date, "end_date")

    # ---------- 特殊值：latest — 按策略配置做每日增量 ----------
    if dataset_name == "latest":
        _run_latest(cfg, codes)
        return

    ds = next((d for d in cfg.datasets if d.name == dataset_name), None)
    if ds is None:
        available = ", ".join(AVAILABLE_DATASETS)
        console.print(f"[red]未知数据集 '{dataset_name}'[/red]")
        console.print(f"[dim]可用数据集: {available}[/dim]")
        console.print(f"[dim]或使用 [bold]finget fetch latest[/bold] 按策略配置更新[/dim]")
        sys.exit(1)

    # 默认日期：不传 start_date 时透传 None 给底层。
    # 底层会智能决定：表有数据从 max_date+1 开始（增量）；空表回溯几天兜底。
    # 首次全量初始化历史数据请显式传 -S。

    # fetch 执行期间抑制 finget 的 INFO 日志（仅保留 WARNING+），让输出干净
    from finget.logging import log as finget_log
    is_quiet = cfg.log_level.upper() == "INFO"
    if is_quiet:
        finget_log.remove()
        from finget.logging import setup_logging
        setup_logging(level="WARNING")

    fetcher = TushareFetcher(cfg.fetcher.tushare)
    desc = DATASET_DESCRIPTIONS.get(dataset_name, "")
    behavior = "按日全市场拉取" if (ds.daily_supported and not codes) else "按标的/区间拉取"
    console.print(f"[cyan]数据集-行为：[/cyan]{desc} - {behavior}")
    if start_date or end_date:
        console.print(f"[cyan]日期范围：[/cyan]{start_date or '*'} ~ {end_date or '*'}")
    console.print(
        f"[cyan]选择数据源：[/cyan]{fetcher.selected_url} "
        f"(response time: {fetcher.selected_response_time:.2f}s)"
    )
    try:
        store = DuckDBStore(cfg.storage)
        strategy = UpdateStrategy(fetcher, store, cfg)
        ts_codes = codes.split(",") if codes else None
        n = strategy.run(
            ds,
            ts_codes,
            start_date=start_date,
            end_date=end_date,
        )
        date_info = ""
        if start_date or end_date:
            date_info = f" ({start_date or '*'} ~ {end_date or '*'})"
        console.print(f"[green]✓ 完成. {n:,} 行已处理.{date_info}[/green]")
    finally:
        if is_quiet:
            finget_log.remove()
            from finget.logging import setup_logging
            setup_logging(level=cfg.log_level)
        store.close()


def _run_latest(cfg: Config, codes: str | None) -> None:
    """按策略配置文件 (config.yaml) 做每日增量更新.

    策略配置文件中的 latest_datasets 列表决定了更新哪些数据集。
    """
    from finget.fetchers.tushare_fetcher import TushareFetcher
    from finget.storage.duckdb_store import DuckDBStore
    from finget.updater.strategies import UpdateStrategy

    ds_names = cfg.strategy.latest_datasets
    if not ds_names:
        console.print("[red]策略配置中 latest_datasets 为空，无可更新的数据集[/red]")
        sys.exit(1)

    console.print(f"[cyan]策略配置 (latest_datasets)：[/cyan]{', '.join(ds_names)}")

    # 抑制 finget 的 INFO 日志（仅保留 WARNING+），让输出干净
    from finget.logging import log as finget_log
    is_quiet = cfg.log_level.upper() == "INFO"
    if is_quiet:
        finget_log.remove()
        from finget.logging import setup_logging
        setup_logging(level="WARNING")

    fetcher = TushareFetcher(cfg.fetcher.tushare)
    store = DuckDBStore(cfg.storage)
    strategy = UpdateStrategy(fetcher, store, cfg)

    console.print(
        f"[cyan]选择数据源：[/cyan]{fetcher.selected_url} "
        f"(response time: {fetcher.selected_response_time:.2f}s)"
    )

    ts_codes = codes.split(",") if codes else None
    started_at = time.monotonic()
    total_rows = 0
    success_count = 0
    failed: list[tuple[str, str]] = []

    for ds_name in ds_names:
        ds = next((d for d in cfg.datasets if d.name == ds_name), None)
        if ds is None:
            console.print(f"  [yellow]⚠ 跳过未知数据集 '{ds_name}'[/yellow]")
            continue

        desc = DATASET_DESCRIPTIONS.get(ds_name, "")
        behavior = "按日全市场拉取" if (ds.daily_supported and not ts_codes) else "按标的/区间拉取"
        console.print(f"\n[cyan]数据集-行为：[/cyan]{desc} - {behavior}")

        try:
            n = strategy.run(ds, ts_codes)
            total_rows += n
            success_count += 1
            console.print(f"  [green]✓ {ds_name}: {n:,} 行已处理[/green]")
        except Exception as e:
            failed.append((ds_name, str(e)))
            console.print(f"  [red]✗ {ds_name} 失败: {e}[/red]")

    elapsed = time.monotonic() - started_at
    console.print(
        f"\n[bold green]✓ latest 完成[/bold green] "
        f"成功 {success_count}/{len(ds_names)} 个数据集, "
        f"共 {total_rows:,} 行, 耗时 [cyan]{elapsed:.1f}s[/cyan]"
    )
    if failed:
        console.print(f"  [red]失败: {', '.join(n for n, _ in failed)}[/red]")

    if is_quiet:
        finget_log.remove()
        from finget.logging import setup_logging
        setup_logging(level=cfg.log_level)
    store.close()


# =========================================================================
# scan — 查漏补缺（按策略配置文件）
# =========================================================================

@main.command()
@click.option(
    "--codes", "-s", default=None, help="指定标的代码（逗号分隔），默认全部标的"
)
def scan(codes: str | None) -> None:
    """按策略配置文件 (config.yaml) 做查漏补缺.

    策略配置文件中的 scan_datasets 列表决定了扫描哪些数据集。
    对每个数据集，对比交易日历找出缺失的交易日数据并自动补齐。

    配置文件示例 (config.yaml):
      scan_datasets:
        - daily
        - weekly
        - adj_factor
        - daily_basic
    """
    cfg = _get_cfg_or_exit()

    from finget.fetchers.tushare_fetcher import TushareFetcher
    from finget.storage.duckdb_store import DuckDBStore
    from finget.updater.strategies import UpdateStrategy

    if cfg.fetcher.tushare is None:
        console.print("[red]No tushare config found.[/red]")
        sys.exit(1)

    ds_names = cfg.strategy.scan_datasets
    if not ds_names:
        console.print("[red]策略配置中 scan_datasets 为空，无可扫描的数据集[/red]")
        sys.exit(1)

    console.print(f"[cyan]策略配置 (scan_datasets):[/cyan] {', '.join(ds_names)}")

    fetcher = TushareFetcher(cfg.fetcher.tushare)
    store = DuckDBStore(cfg.storage)
    strategy = UpdateStrategy(fetcher, store, cfg)

    ts_codes = codes.split(",") if codes else None
    started_at = time.monotonic()
    total_rows = 0

    for ds_name in ds_names:
        ds = next((d for d in cfg.datasets if d.name == ds_name), None)
        if ds is None:
            console.print(f"[yellow]⚠ 跳过未知数据集 '{ds_name}'[/yellow]")
            continue

        desc = DATASET_DESCRIPTIONS.get(ds_name, "")
        console.print(f"\n[cyan]▶ {ds_name}[/cyan] — {desc}")

        try:
            n = strategy.run_scan(ds, ts_codes)
            if n == 0:
                console.print(f"  [dim]✓ {ds_name}: 无需补齐[/dim]")
            else:
                total_rows += n
                console.print(f"  [green]✓ {ds_name}: 补齐 {n:,} 行[/green]")
        except Exception as e:
            log.error(f"{ds_name} 扫描失败: {e}")
            console.print(f"  [red]✗ {ds_name} 失败: {e}[/red]")

    elapsed = time.monotonic() - started_at
    console.print(f"\n[bold green]✓ scan 完成[/bold green] 共补齐 {total_rows:,} 行 耗时 [cyan]{elapsed:.1f}s[/cyan]")
    store.close()


# =========================================================================
# stats — 数据统计（顶级命令）
# =========================================================================

@main.command()
def stats() -> None:
    """显示数据统计（行数、标的数、日期范围、覆盖率）.

    展示每张表的: 总行数 / 标的数 / 最早日期 / 最新日期 / 覆盖率
    """
    cfg = _get_cfg_or_exit()
    from finget.stats.collector import StatsCollector
    from finget.storage.duckdb_store import DuckDBStore

    store = DuckDBStore(cfg.storage)
    collector = StatsCollector(store)
    collector.print_summary()
    store.close()


# =========================================================================
# show — 查看表内容（顶级命令）
# =========================================================================

@main.command()
@click.argument("table_name")
@click.option("--output", "-o", default=None, help="导出为 CSV 文件路径")
@click.option("--limit", "-n", default=20, show_default=True, help="显示行数")
def show(table_name: str, output: str | None, limit: int) -> None:
    """查看数据表内容.

    TABLE_NAME 是要查看的表名（如 daily, stock_basic 等）。
    默认显示前 20 行，可用 -n 指定行数，-o 导出为 CSV。
    """
    cfg = _get_cfg_or_exit()
    from finget.reader.data_reader import DataReader
    from finget.storage.duckdb_store import DuckDBStore

    store = DuckDBStore(cfg.storage)
    reader = DataReader(store)
    df = reader.raw_query(f"SELECT * FROM {table_name} LIMIT {limit};")
    if output:
        df.to_csv(output, index=False)
        console.print(f"[green]✓ 已导出到 {output}[/green]")
    else:
        console.print(df.to_string())
    store.close()


# =========================================================================
# serve — 启动 Web 数据展示前端
# =========================================================================

@main.command()
@click.option(
    "--host",
    default="0.0.0.0",
    show_default=True,
    help="绑定的 IP 地址（0.0.0.0 监听所有网卡，127.0.0.1 仅本地）",
)
@click.option(
    "--port",
    "-p",
    default=8000,
    show_default=True,
    type=int,
    help="端口号",
)
@click.option(
    "--reload",
    is_flag=True,
    default=False,
    help="开启热重载（开发模式，代码变更自动重启）",
)
def serve(host: str, port: int, reload: bool) -> None:
    """启动 Web 数据展示前端.

    提供仪表盘、K线分析、研报中心、机构调研、券商金股
    等数据展示页面。

    默认监听 0.0.0.0:8000（局域网可访问），使用 --host 127.0.0.1 仅本地。

    示例:
      finget serve                     # 默认 127.0.0.1:8000
      finget serve -p 9000             # 指定端口
      finget serve --host 0.0.0.0      # 局域网可访问
      finget serve --reload            # 开发模式热重载
    """
    import uvicorn

    console.print(f"[bold cyan]🚀 finget Dashboard 启动中...[/bold cyan]")
    console.print(f"   地址: [cyan]http://{host}:{port}[/cyan]")
    console.print(f"   仪表盘: [dim]http://{host}:{port}/[/dim]")
    console.print(f"   K线分析: [dim]http://{host}:{port}/kline[/dim]")
    console.print(f"   研报中心: [dim]http://{host}:{port}/research[/dim]")
    console.print(f"   机构调研: [dim]http://{host}:{port}/survey[/dim]")
    console.print(f"   券商金股: [dim]http://{host}:{port}/broker[/dim]")
    console.print("[dim]按 Ctrl+C 停止服务[/dim]")

    uvicorn.run(
        "finget.server.app:app",
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
