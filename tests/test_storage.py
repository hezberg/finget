"""存储层测试."""

import pandas as pd
import pytest
from datetime import date

from finget.storage.duckdb_store import DuckDBStore, TIME_SERIES_DATASETS


class TestDuckDBStore:
    def test_init_all(self, store: DuckDBStore):
        tables = store.list_tables()
        assert "stock_basic" in tables
        assert "daily" in tables
        assert "weekly" in tables
        assert "adj_factor" in tables
        assert "daily_basic" in tables
        assert "trade_cal" in tables
        assert "report_rc" in tables

    def test_table_exists(self, store: DuckDBStore):
        assert store.table_exists("daily")
        assert not store.table_exists("nonexistent")

    def test_upsert_daily(self, store: DuckDBStore, sample_daily_df):
        n = store.upsert("daily", sample_daily_df)
        assert n == 3
        assert store.count_rows("daily") == 3

    def test_upsert_idempotent(self, store: DuckDBStore, sample_daily_df):
        """重复写入相同数据应幂等（不增加行数）."""
        store.upsert("daily", sample_daily_df)
        store.upsert("daily", sample_daily_df)
        assert store.count_rows("daily") == 3  # 去重

    def test_upsert_update_on_conflict(self, store: DuckDBStore, sample_daily_df):
        """冲突时应更新已有行."""
        store.upsert("daily", sample_daily_df)
        # 修改 close 价格
        updated = sample_daily_df.copy()
        updated.loc[0, "close"] = 99.99
        store.upsert("daily", updated)
        assert store.count_rows("daily") == 3
        df = store.query("SELECT * FROM daily WHERE ts_code='000001.SZ' AND trade_date='2024-01-01';")
        assert len(df) == 1
        assert df.iloc[0]["close"] == pytest.approx(99.99)

    def test_upsert_empty_df(self, store: DuckDBStore):
        n = store.upsert("daily", pd.DataFrame())
        assert n == 0

    def test_upsert_unknown_table(self, store: DuckDBStore, sample_daily_df):
        with pytest.raises(ValueError, match="does not exist"):
            store.upsert("nonexistent", sample_daily_df)

    def test_get_max_date(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        max_d = store.get_max_date("daily")
        assert max_d == date(2024, 1, 2)

        max_d_code = store.get_max_date("daily", "000001.SZ")
        assert max_d_code == date(2024, 1, 2)

    def test_get_min_date(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        min_d = store.get_min_date("daily")
        assert min_d == date(2024, 1, 1)

    def test_get_existing_dates(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        dates = store.get_existing_dates("daily", "000001.SZ", "20240101", "20240102")
        assert dates == {date(2024, 1, 1), date(2024, 1, 2)}

    def test_count_rows_empty(self, store: DuckDBStore):
        assert store.count_rows("daily") == 0
        assert store.count_rows("nonexistent") == 0

    def test_query(self, store: DuckDBStore, sample_daily_df):
        store.upsert("daily", sample_daily_df)
        df = store.query("SELECT * FROM daily WHERE ts_code = ? ORDER BY trade_date;", ["000001.SZ"])
        assert len(df) == 2
        assert list(df["ts_code"]) == ["000001.SZ", "000001.SZ"]

    def test_stock_basic_upsert(self, store: DuckDBStore, sample_stock_basic_df):
        n = store.upsert("stock_basic", sample_stock_basic_df)
        assert n == 2
        assert store.count_rows("stock_basic") == 2

    def test_stock_basic_idempotent(self, store: DuckDBStore, sample_stock_basic_df):
        store.upsert("stock_basic", sample_stock_basic_df)
        store.upsert("stock_basic", sample_stock_basic_df)
        assert store.count_rows("stock_basic") == 2

    def test_drop_table(self, store: DuckDBStore):
        assert store.table_exists("daily")
        store.drop_table("daily")
        assert not store.table_exists("daily")

    def test_upsert_drops_unknown_columns(self, store: DuckDBStore):
        """DataFrame 含表 schema 中不存在的列时,应剔除并 log warning.

        场景: tushare stock_basic 接口升级后多了 'fullname' 字段,
        但旧 schema 没有该字段 — upsert 应优雅降级.
        """
        # stock_basic schema 现在含 fullname, 这里人为模拟 DataFrame 多一列
        from finget.logging import log
        from loguru import logger

        captured = []
        handler_id = logger.add(lambda msg: captured.append(str(msg)), level="WARNING")
        try:
            df = pd.DataFrame({
                "ts_code": ["000001.SZ", "600000.SH"],
                "symbol": ["000001", "600000"],
                "name": ["平安银行", "浦发银行"],
                "fullname": ["平安银行股份有限公司", "上海浦东发展银行股份有限公司"],
                "new_tushare_field": ["v1", "v2"],  # 表里没有
                "another_new": [1, 2],  # 表里没有
            })
            n = store.upsert("stock_basic", df, conflict_keys=["ts_code"])
        finally:
            logger.remove(handler_id)

        assert n == 2
        # 未知列被剔除, 写入应成功
        assert store.count_rows("stock_basic") == 2
        # 应有 warning 日志（loguru 捕获）
        assert any("new_tushare_field" in m for m in captured), f"captured: {captured}"

    def test_upsert_fills_missing_columns_with_none(self, store: DuckDBStore):
        """DataFrame 缺少表 schema 中的列时,应自动用 None 填充."""
        df = pd.DataFrame({
            "ts_code": ["000001.SZ"],
            "symbol": ["000001"],
            "name": ["平安银行"],
            # 缺少很多列: fullname, enname, cnspell, area, industry, ...
        })
        n = store.upsert("stock_basic", df, conflict_keys=["ts_code"])
        assert n == 1
        # 缺失列应为 None
        result = store.query("SELECT fullname, area, industry, enname FROM stock_basic WHERE ts_code='000001.SZ'")
        assert result.iloc[0]["fullname"] is None
        assert result.iloc[0]["enname"] is None
        assert result.iloc[0]["area"] is None

    def test_get_table_columns(self, store: DuckDBStore):
        """get_table_columns 应返回表的列名列表."""
        cols = store.get_table_columns("stock_basic")
        # 17 列（schema 中定义的）
        assert "ts_code" in cols
        assert "name" in cols
        assert "fullname" in cols
        assert "enname" in cols
        assert "cnspell" in cols
        assert "act_name" in cols
        assert "act_ent_type" in cols
        assert "is_hs" in cols
        assert len(cols) == 17

    def test_get_table_columns_nonexistent(self, store: DuckDBStore):
        """不存在的表应返回空列表（不报错）."""
        cols = store.get_table_columns("nonexistent_table")
        assert cols == []

    def test_file_based_store(self, tmp_path):
        """测试文件型 DB（非内存）."""
        from finget.config import StorageConfig

        cfg = StorageConfig(db_path=str(tmp_path / "test.duckdb"))
        s = DuckDBStore(cfg)
        s.init_all()
        assert s.table_exists("daily")
        s.close()
        # 重新打开
        s2 = DuckDBStore(cfg)
        assert s2.table_exists("daily")
        s2.close()


class TestTradeCal:
    """trade_cal 交易日历表测试."""

    def _sample_cal(self) -> pd.DataFrame:
        return pd.DataFrame({
            "exchange": ["SSE", "SSE", "SSE", "SSE"],
            "cal_date": ["20240101", "20240102", "20240103", "20240104"],
            "is_open": [True, True, True, False],
            "pretrade_date": [
                pd.NaT, "20231229", "20240102", "20240103",
            ],
        })

    def test_trade_cal_init(self, store: DuckDBStore):
        """init_all() 应创建 trade_cal 表."""
        assert store.table_exists("trade_cal")

    def test_trade_cal_upsert(self, store: DuckDBStore):
        """trade_cal 写入成功."""
        df = self._sample_cal()
        df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        df["is_open"] = df["is_open"].astype(bool)
        n = store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])
        assert n == 4
        assert store.count_rows("trade_cal") == 4

    def test_trade_cal_idempotent(self, store: DuckDBStore):
        """重复写入相同 (exchange, cal_date) 应幂等."""
        df = self._sample_cal()
        df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        df["is_open"] = df["is_open"].astype(bool)
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])
        assert store.count_rows("trade_cal") == 4

    def test_trade_cal_different_exchanges(self, store: DuckDBStore):
        """不同交易所相同日期应独立存储."""
        df = pd.DataFrame({
            "exchange": ["SSE", "SZSE"],
            "cal_date": ["20240101", "20240101"],
            "is_open": [True, True],
            "pretrade_date": [pd.NaT, pd.NaT],
        })
        df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        df["is_open"] = df["is_open"].astype(bool)
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])
        assert store.count_rows("trade_cal") == 2

    def test_trade_cal_update_on_conflict(self, store: DuckDBStore):
        """同一 (exchange, cal_date) 的后续写入应更新 is_open 等字段."""
        df1 = pd.DataFrame({
            "exchange": ["SSE"],
            "cal_date": [date(2024, 1, 1)],
            "is_open": [True],
            "pretrade_date": [None],
        })
        df2 = pd.DataFrame({
            "exchange": ["SSE"],
            "cal_date": [date(2024, 1, 1)],
            "is_open": [False],  # 改为休市
            "pretrade_date": [None],
        })
        store.upsert("trade_cal", df1, conflict_keys=["exchange", "cal_date"])
        store.upsert("trade_cal", df2, conflict_keys=["exchange", "cal_date"])
        assert store.count_rows("trade_cal") == 1
        result = store.query("SELECT is_open FROM trade_cal WHERE exchange='SSE' AND cal_date='2024-01-01'")
        assert bool(result.iloc[0]["is_open"]) is False

    def test_get_trade_dates(self, store: DuckDBStore):
        """get_trade_dates 应返回交易日列表."""
        df = self._sample_cal()
        df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        df["is_open"] = df["is_open"].astype(bool)
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])

        # 默认 is_open=True，只返回开放日
        dates = store.get_trade_dates(exchange="SSE")
        assert len(dates) == 3
        assert date(2024, 1, 4) not in dates  # 休市日被排除

    def test_get_trade_dates_with_range(self, store: DuckDBStore):
        """get_trade_dates 支持日期范围过滤."""
        df = self._sample_cal()
        df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        df["is_open"] = df["is_open"].astype(bool)
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])

        dates = store.get_trade_dates(exchange="SSE", start="20240102", end="20240103")
        assert dates == [date(2024, 1, 2), date(2024, 1, 3)]

    def test_get_trade_dates_includes_closed(self, store: DuckDBStore):
        """is_open=False 时返回全部日期（含休市日）."""
        df = self._sample_cal()
        df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        df["is_open"] = df["is_open"].astype(bool)
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])

        dates = store.get_trade_dates(exchange="SSE", is_open=False)
        assert len(dates) == 1
        assert dates == [date(2024, 1, 4)]

    def test_get_trade_dates_empty_when_no_table(self):
        """trade_cal 表不存在时返回空列表（不报错）."""
        from finget.config import StorageConfig

        cfg = StorageConfig(db_path=":memory:")
        s = DuckDBStore(cfg)
        # 不调用 init_all
        dates = s.get_trade_dates(exchange="SSE")
        assert dates == []
        s.close()

    def test_get_trade_dates_sorted_asc(self, store: DuckDBStore):
        """返回值应按日期升序."""
        df = pd.DataFrame({
            "exchange": ["SSE"] * 4,
            "cal_date": [date(2024, 1, 3), date(2024, 1, 1), date(2024, 1, 4), date(2024, 1, 2)],
            "is_open": [True] * 4,
            "pretrade_date": [None] * 4,
        })
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])
        dates = store.get_trade_dates(exchange="SSE")
        assert dates == sorted(dates)
        assert dates[0] == date(2024, 1, 1)
        assert dates[-1] == date(2024, 1, 4)

    def test_trade_cal_calendar_in_known_set(self):
        """CALENDAR_DATASETS 应包含 trade_cal."""
        from finget.storage.duckdb_store import CALENDAR_DATASETS
        assert "trade_cal" in CALENDAR_DATASETS
        # TIME_SERIES_DATASETS 不应包含 trade_cal
        assert "trade_cal" not in TIME_SERIES_DATASETS

    def test_get_cal_date_range(self, store: DuckDBStore):
        """get_cal_date_range 应返回指定交易所的 (min, max) 日期."""
        df = pd.DataFrame({
            "exchange": ["SSE", "SSE", "SSE", "SZSE", "SZSE"],
            "cal_date": ["20240101", "20240102", "20240103", "20240401", "20240402"],
            "is_open": [True, True, True, True, True],
            "pretrade_date": [None, "20231229", "20240102", "20240329", "20240401"],
        })
        df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
        df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        df["is_open"] = df["is_open"].astype(bool)
        store.upsert("trade_cal", df, conflict_keys=["exchange", "cal_date"])

        # SSE: 20240101 ~ 20240103
        sse_min, sse_max = store.get_cal_date_range("SSE")
        assert sse_min == date(2024, 1, 1)
        assert sse_max == date(2024, 1, 3)

        # SZSE: 20240401 ~ 20240402
        szse_min, szse_max = store.get_cal_date_range("SZSE")
        assert szse_min == date(2024, 4, 1)
        assert szse_max == date(2024, 4, 2)

    def test_get_cal_date_range_empty_exchange(self, store: DuckDBStore):
        """指定交易所无数据时返回 (None, None)."""
        result = store.get_cal_date_range("NONEXISTENT")
        assert result == (None, None)

    def test_get_cal_date_range_table_not_exist(self):
        """trade_cal 表不存在时返回 (None, None)."""
        from finget.config import StorageConfig
        cfg = StorageConfig(db_path=":memory:")
        s = DuckDBStore(cfg)
        try:
            result = s.get_cal_date_range("SSE")
            assert result == (None, None)
        finally:
            s.close()


class TestStkSurvUpsert:
    """stk_surv / stk_surv_detail 表 upsert 幂等性测试（按 3 列去重键）."""

    def test_stk_surv_upsert_idempotent(self, store: DuckDBStore):
        """stk_surv 按 (ts_code, surv_date, rece_org) 去重，重复 upsert 行数不变."""
        store.init_table("stk_surv")
        df = pd.DataFrame({
            "ts_code": ["002223.SZ", "002223.SZ"],
            "name": ["鱼跃医疗", "鱼跃医疗"],
            "surv_date": pd.to_datetime(["20211024", "20211025"], format="%Y%m%d").date,
            "fund_visitors": ["机构A", "机构B"],
            "rece_place": ["会议室", "线上"],
            "rece_mode": ["实地", "电话"],
            "rece_org": ["机构A", "机构B"],
            "org_type": ["公募", "券商"],
            "comp_rece": ["董秘", "总经理"],
        })
        n = store.upsert("stk_surv", df, conflict_keys=["ts_code", "surv_date", "rece_org"])
        assert n == 2
        assert store.count_rows("stk_surv") == 2
        # 再写一次（幂等）
        store.upsert("stk_surv", df, conflict_keys=["ts_code", "surv_date", "rece_org"])
        assert store.count_rows("stk_surv") == 2

    def test_stk_surv_detail_upsert_idempotent(self, store: DuckDBStore):
        """stk_surv_detail 按 (ts_code, surv_date, rece_org) 去重."""
        store.init_table("stk_surv_detail")
        df = pd.DataFrame({
            "ts_code": ["002223.SZ"],
            "surv_date": pd.to_datetime(["20211024"], format="%Y%m%d").date,
            "rece_org": ["机构A"],
            "content": ["很长的调研内容" * 100],
        })
        store.upsert("stk_surv_detail", df, conflict_keys=["ts_code", "surv_date", "rece_org"])
        assert store.count_rows("stk_surv_detail") == 1
        # 再写一次（幂等）
        store.upsert("stk_surv_detail", df, conflict_keys=["ts_code", "surv_date", "rece_org"])
        assert store.count_rows("stk_surv_detail") == 1

    def test_stk_surv_same_date_diff_org(self, store: DuckDBStore):
        """同股票同日不同机构应各保留一条（去重键含 rece_org）."""
        store.init_table("stk_surv")
        df = pd.DataFrame({
            "ts_code": ["002223.SZ", "002223.SZ"],
            "name": ["鱼跃医疗", "鱼跃医疗"],
            "surv_date": pd.to_datetime(["20211024", "20211024"], format="%Y%m%d").date,
            "fund_visitors": ["机构A", "机构B"],
            "rece_place": ["会议室", "线上"],
            "rece_mode": ["实地", "电话"],
            "rece_org": ["机构A", "机构B"],  # 同日不同机构
            "org_type": ["公募", "券商"],
            "comp_rece": ["董秘", "总经理"],
        })
        store.upsert("stk_surv", df, conflict_keys=["ts_code", "surv_date", "rece_org"])
        assert store.count_rows("stk_surv") == 2


class TestHkUsBasicUpsert:
    """hk_us_basic 表 upsert 幂等性测试（按 ts_code 去重）."""

    def test_hk_us_basic_upsert_idempotent(self, store: DuckDBStore):
        """hk_us_basic 按 ts_code 去重，重复 upsert 行数不变."""
        store.init_table("hk_us_basic")
        df = pd.DataFrame({
            "ts_code": ["00700.HK", "AAPL.US"],
            "name": ["腾讯控股", "苹果"],
            "enname": ["TENCENT", "Apple Inc"],
        })
        store.upsert("hk_us_basic", df, conflict_keys=["ts_code"])
        assert store.count_rows("hk_us_basic") == 2
        # 再写一次（幂等）
        store.upsert("hk_us_basic", df, conflict_keys=["ts_code"])
        assert store.count_rows("hk_us_basic") == 2

    def test_hk_us_basic_hk_us_no_conflict(self, store: DuckDBStore):
        """港股和美股 ts_code 后缀不同，不会冲突."""
        store.init_table("hk_us_basic")
        hk_df = pd.DataFrame({
            "ts_code": ["00700.HK"], "name": ["腾讯"], "enname": ["TENCENT"],
        })
        us_df = pd.DataFrame({
            "ts_code": ["00700.US"], "name": ["另一只"], "enname": ["Other"],
        })
        store.upsert("hk_us_basic", hk_df, conflict_keys=["ts_code"])
        store.upsert("hk_us_basic", us_df, conflict_keys=["ts_code"])
        assert store.count_rows("hk_us_basic") == 2
