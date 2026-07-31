"""数据状态统计 — 已下载/未下载量、覆盖率等."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd
from rich.console import Console
from rich.table import Table

from finget.storage.duckdb_store import (
    TIME_SERIES_DATASETS,
    SURVEY_DATASETS,
    DuckDBStore,
)

# 研报类数据集（有 ts_code + report_date，需统计日期范围但日期列名不同）
RESEARCH_DATASETS = {"report_rc"}
# 研报数据集的日期列名映射
RESEARCH_DATE_COL = {"report_rc": "report_date"}

# 券商月度金股类数据集（有 ts_code + month，需统计月份范围）
BROKER_DATASETS = {"broker_recommend"}

# 调研类数据集日期列名映射
SURVEY_DATE_COL = "surv_date"


@dataclass
class TableStats:
    """单表统计信息."""

    table_name: str
    total_rows: int
    num_codes: int
    min_date: date | None
    max_date: date | None
    # 预期交易日数（基于日历），None 表示无法计算
    expected_dates: int | None = None
    actual_dates: int | None = None
    coverage_pct: float | None = None


class StatsCollector:
    """数据统计收集器."""

    def __init__(self, store: DuckDBStore) -> None:
        self.store = store
        self.console = Console()

    def collect(self) -> list[TableStats]:
        """收集所有表的统计信息.

        基础数据表（stock_basic/trade_cal/hk_us_basic）排在最上面，
        其余按最新日期降序（数据越新越靠前，无日期的排最后）。
        """
        tables = self.store.list_tables()
        priority = ["stock_basic", "trade_cal", "hk_us_basic"]
        priority_set = set(priority)

        # 先收集所有表的统计（含 max_date）
        all_stats: list[TableStats] = []
        for table in tables:
            all_stats.append(self._collect_table(table))

        # 分两组：基础表 + 其余表
        basic = [s for s in all_stats if s.table_name in priority_set]
        rest = [s for s in all_stats if s.table_name not in priority_set]

        # 基础表按 priority 顺序
        basic.sort(key=lambda s: priority.index(s.table_name))
        # 其余表按 max_date 降序（None/NaT/空 排最后）
        # 用字符串排序避免 date/Timestamp/NaT 混合类型比较问题
        def _rest_key(s: TableStats) -> str:
            md = s.max_date
            if md is None:
                return ""
            try:
                return str(md)[:10]  # YYYY-MM-DD
            except Exception:
                return ""
        rest.sort(key=_rest_key, reverse=True)

        return basic + rest

    def _collect_table(self, table: str) -> TableStats:
        total = self.store.count_rows(table)

        # 判断是否时序表
        is_ts = table in TIME_SERIES_DATASETS
        is_research = table in RESEARCH_DATASETS
        is_broker = table in BROKER_DATASETS

        if is_ts:
            df = self.store.query(
                f"""
                SELECT
                    count(DISTINCT ts_code) as num_codes,
                    min(trade_date) as min_d,
                    max(trade_date) as max_d,
                    count(DISTINCT trade_date) as num_dates
                FROM {table}
                """
            )
            if df.empty:
                return TableStats(table, 0, 0, None, None)
            row = df.iloc[0]
            return TableStats(
                table_name=table,
                total_rows=total,
                num_codes=int(row["num_codes"]),
                min_date=row["min_d"],
                max_date=row["max_d"],
                actual_dates=int(row["num_dates"]),
            )
        elif is_research:
            date_col = RESEARCH_DATE_COL.get(table, "report_date")
            df = self.store.query(
                f"""
                SELECT
                    count(DISTINCT ts_code) as num_codes,
                    min({date_col}) as min_d,
                    max({date_col}) as max_d,
                    count(DISTINCT {date_col}) as num_dates
                FROM {table}
                """
            )
            if df.empty:
                return TableStats(table, 0, 0, None, None)
            row = df.iloc[0]
            return TableStats(
                table_name=table,
                total_rows=total,
                num_codes=int(row["num_codes"]),
                min_date=row["min_d"],
                max_date=row["max_d"],
                actual_dates=int(row["num_dates"]),
            )
        elif is_broker:
            # 券商金股：month 是 VARCHAR (YYYYMM)，统计月份范围和标的数/券商数
            df = self.store.query(
                f"""
                SELECT
                    count(DISTINCT ts_code) as num_codes,
                    count(DISTINCT broker) as num_brokers,
                    min(month) as min_month,
                    max(month) as max_month,
                    count(DISTINCT month) as num_months
                FROM {table}
                """
            )
            if df.empty:
                return TableStats(table, 0, 0, None, None)
            row = df.iloc[0]
            # 把 YYYYMM 转成日期用于显示（取月份第一天）
            min_m = row["min_month"]
            max_m = row["max_month"]
            min_date = date(int(min_m[:4]), int(min_m[4:6]), 1) if min_m else None
            max_date = date(int(max_m[:4]), int(max_m[4:6]), 1) if max_m else None
            return TableStats(
                table_name=table,
                total_rows=total,
                num_codes=int(row["num_codes"]),
                min_date=min_date,
                max_date=max_date,
                actual_dates=int(row["num_months"]),
            )
        elif table in SURVEY_DATASETS:
            # 机构调研：用 surv_date 做日期列，stk_surv_detail 是附属表
            if table == "stk_surv_detail":
                num = self.store.query(f"SELECT count(*) as c FROM {table}").iloc[0]["c"]
                return TableStats(
                    table_name=table,
                    total_rows=total,
                    num_codes=0,
                    min_date=None,
                    max_date=None,
                )
            date_col = SURVEY_DATE_COL
            df = self.store.query(
                f"""
                SELECT
                    count(DISTINCT ts_code) as num_codes,
                    min({date_col}) as min_d,
                    max({date_col}) as max_d,
                    count(DISTINCT {date_col}) as num_dates
                FROM {table}
                """
            )
            if df.empty:
                return TableStats(table, 0, 0, None, None)
            row = df.iloc[0]
            return TableStats(
                table_name=table,
                total_rows=total,
                num_codes=int(row["num_codes"]),
                min_date=row["min_d"],
                max_date=row["max_d"],
                actual_dates=int(row["num_dates"]),
            )
        else:
            num = self.store.query(f"SELECT count(*) as c FROM {table}").iloc[0]["c"]
            return TableStats(
                table_name=table,
                total_rows=total,
                num_codes=int(num),
                min_date=None,
                max_date=None,
            )

    def print_summary(self) -> None:
        """打印统计摘要表."""
        stats = self.collect()
        if not stats:
            self.console.print("[yellow]No tables found. Run 'finget init-db' first.[/yellow]")
            return

        table = Table(title="finget 数据统计", show_lines=True)
        table.add_column("表名", style="cyan")
        table.add_column("总行数", justify="right", style="green")
        table.add_column("标的数", justify="right")
        table.add_column("最早日期")
        table.add_column("最新日期")
        table.add_column("交易日数", justify="right")
        table.add_column("覆盖率", justify="right")

        for s in stats:
            coverage = (
                f"{s.coverage_pct:.1f}%" if s.coverage_pct is not None else "-"
            )
            table.add_row(
                s.table_name,
                f"{s.total_rows:,}",
                f"{s.num_codes:,}",
                str(s.min_date or "-"),
                str(s.max_date or "-"),
                f"{s.actual_dates:,}" if s.actual_dates else "-",
                coverage,
            )
        self.console.print(table)

    def missing_report(
        self,
        table_name: str,
        ts_codes: list[str] | None = None,
        trade_calendar: set[date] | None = None,
    ) -> pd.DataFrame:
        """生成数据缺失报告.

        Args:
            table_name: 时序数据表名.
            ts_codes: 待检查的标的; None 则全部.
            trade_calendar: 交易日历集合; None 则不校验覆盖率.

        Returns:
            DataFrame: [ts_code, expected, actual, missing, coverage_pct]
        """
        if ts_codes is None:
            df = self.store.query(
                f"SELECT DISTINCT ts_code FROM {table_name} ORDER BY ts_code;"
            )
            ts_codes = df["ts_code"].tolist() if not df.empty else []

        report_rows: list[dict[str, object]] = []
        for code in ts_codes:
            min_d = self.store.get_min_date(table_name, code)
            max_d = self.store.get_max_date(table_name, code)
            if min_d is None or max_d is None:
                report_rows.append({
                    "ts_code": code,
                    "min_date": None,
                    "max_date": None,
                    "actual": 0,
                    "expected": 0,
                    "missing": 0,
                    "coverage_pct": 0.0,
                })
                continue
            existing = self.store.get_existing_dates(table_name, code, min_d, max_d)
            actual = len(existing)
            if trade_calendar:
                expected = len({d for d in trade_calendar if min_d <= d <= max_d})
            else:
                expected = actual
            missing = max(expected - actual, 0)
            pct = (actual / expected * 100) if expected > 0 else 0.0
            report_rows.append({
                "ts_code": code,
                "min_date": min_d,
                "max_date": max_d,
                "actual": actual,
                "expected": expected,
                "missing": missing,
                "coverage_pct": round(pct, 2),
            })
        return pd.DataFrame(report_rows)
