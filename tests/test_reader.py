"""读取层测试."""

import pandas as pd
import pytest
from datetime import date

from finget.reader.data_reader import DataReader
from finget.storage.duckdb_store import DuckDBStore


class TestDataReader:
    def test_get_kline_all(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        reader = DataReader(store)
        df = reader.get_kline()
        assert len(df) == 3

    def test_get_kline_by_code(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        reader = DataReader(store)
        df = reader.get_kline(ts_code="000001.SZ")
        assert len(df) == 2
        assert all(df["ts_code"] == "000001.SZ")

    def test_get_kline_by_codes(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        reader = DataReader(store)
        df = reader.get_kline(ts_code=["000001.SZ", "600000.SH"])
        assert len(df) == 3

    def test_get_kline_by_date_range(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        reader = DataReader(store)
        df = reader.get_kline(start_date="20240102")
        assert len(df) == 1

    def test_get_close(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        reader = DataReader(store)
        s = reader.get_close("000001.SZ")
        assert len(s) == 2
        assert s.name == "000001.SZ"

    def test_get_close_empty(self, store: DuckDBStore):
        reader = DataReader(store)
        s = reader.get_close("NONEXIST.SZ")
        assert s.empty

    def test_get_stock_basic(self, store: DuckDBStore, sample_stock_basic_df):
        store.upsert("stock_basic", sample_stock_basic_df)
        reader = DataReader(store)
        df = reader.get_stock_basic()
        assert len(df) == 2

    def test_get_stock_basic_by_industry(self, store: DuckDBStore, sample_stock_basic_df):
        store.upsert("stock_basic", sample_stock_basic_df)
        reader = DataReader(store)
        df = reader.get_stock_basic(industry="银行")
        assert len(df) == 2

    def test_get_stock_list(self, store: DuckDBStore, sample_stock_basic_df):
        store.upsert("stock_basic", sample_stock_basic_df)
        reader = DataReader(store)
        codes = reader.get_stock_list()
        assert "000001.SZ" in codes
        assert "600000.SH" in codes

    def test_get_stock_list_empty(self, store: DuckDBStore):
        reader = DataReader(store)
        assert reader.get_stock_list() == []

    def test_raw_query(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        reader = DataReader(store)
        df = reader.raw_query("SELECT count(*) as c FROM daily;")
        assert df.iloc[0]["c"] == 3

    def test_list_tables(self, store: DuckDBStore):
        reader = DataReader(store)
        tables = reader.list_tables()
        assert "daily" in tables
