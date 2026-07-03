"""统计层测试."""

import pandas as pd
import pytest
from datetime import date

from finget.stats.collector import StatsCollector, TableStats
from finget.storage.duckdb_store import DuckDBStore


class TestStatsCollector:
    def test_collect_empty(self, store: DuckDBStore):
        collector = StatsCollector(store)
        stats = collector.collect()
        assert len(stats) > 0  # tables exist but empty
        for s in stats:
            assert s.total_rows == 0

    def test_collect_with_data(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        collector = StatsCollector(store)
        stats = collector.collect()

        daily_stat = next(s for s in stats if s.table_name == "daily")
        assert daily_stat.total_rows == 3
        assert daily_stat.num_codes == 2
        assert daily_stat.actual_dates == 2  # 20240101, 20240102

    def test_print_summary(self, store: DuckDBStore, sample_daily_df):
        """仅验证不抛异常."""
        store.upsert("daily", sample_daily_df)
        collector = StatsCollector(store)
        collector.print_summary()  # 应正常输出

    def test_missing_report(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        collector = StatsCollector(store)
        report = collector.missing_report("daily", ts_codes=["000001.SZ", "600000.SH"])
        assert len(report) == 2
        assert "coverage_pct" in report.columns

    def test_missing_report_no_data(self, store: DuckDBStore):
        collector = StatsCollector(store)
        report = collector.missing_report("daily", ts_codes=["NONEXIST.SZ"])
        assert len(report) == 1
        assert report.iloc[0]["actual"] == 0
