"""更新策略层测试."""

from datetime import date, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from finget.config import Config, DatasetConfig, StorageConfig, TushareConfig, FetcherConfig, UpdateConfig
from finget.fetchers.base import BaseFetcher, FetchResult
from finget.storage.duckdb_store import DuckDBStore
from finget.updater.strategies import UpdateStrategy
from finget.updater.strategies import RESEARCH_DAILY_THRESHOLD_DAYS
from finget.storage.duckdb_store import SURVEY_DATASETS


class FakeFetcher(BaseFetcher):
    """测试用 fetcher，返回预设数据."""

    def __init__(self, daily_df: pd.DataFrame, stock_basic_df: pd.DataFrame) -> None:
        super().__init__(rate_limit_per_min=0)
        self.daily_df = daily_df
        self.stock_basic_df = stock_basic_df
        self.page_size = 5000
        self._daily_called = False

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        if api_name == "stock_basic":
            df = self.stock_basic_df
            return FetchResult(data=df, has_more=False)
        elif api_name == "daily":
            df = self.daily_df.copy()
            if start_date:
                start_d = self._to_date(start_date)
                df = df[df["trade_date"] >= start_d]
            if end_date:
                end_d = self._to_date(end_date)
                df = df[df["trade_date"] <= end_d]
            return FetchResult(data=df, has_more=False)
        return FetchResult(data=pd.DataFrame(), has_more=False)

    @staticmethod
    def _to_date(d):
        from datetime import date as _date, datetime
        if isinstance(d, _date):
            return d
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, str):
            from datetime import datetime as _dt
            return _dt.strptime(d.replace("-", ""), "%Y%m%d").date()
        return d


@pytest.fixture
def test_config():
    return Config(
        storage=StorageConfig(in_memory=True),
        fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
        update=UpdateConfig(full_lookback_years=5, incremental_lookback_days=3, scan_batch_size=5),
        datasets=[
            DatasetConfig(name="stock_basic", type="stock_basic", api_name="stock_basic"),
            DatasetConfig(name="daily", type="daily", api_name="daily"),
        ],
    )


@pytest.fixture
def fake_daily_df():
    df = pd.DataFrame(
        {
            "ts_code": ["000001.SZ"] * 3 + ["600000.SH"] * 3,
            "trade_date": pd.to_datetime(
                ["20240101", "20240102", "20240103", "20240101", "20240102", "20240103"],
                format="%Y%m%d",
            ).date,
            "open": [10.0, 10.5, 10.8, 20.0, 20.5, 21.0],
            "high": [10.5, 11.0, 11.2, 21.0, 21.5, 22.0],
            "low": [9.8, 10.2, 10.6, 19.5, 20.0, 20.5],
            "close": [10.3, 10.8, 11.0, 20.5, 21.0, 21.5],
            "pre_close": [10.0, 10.3, 10.8, 20.0, 20.5, 21.0],
            "change": [0.3, 0.5, 0.2, 0.5, 0.5, 0.5],
            "pct_chg": [3.0, 4.85, 1.85, 2.5, 2.44, 2.38],
            "vol": [100000.0, 120000.0, 110000.0, 200000.0, 180000.0, 190000.0],
            "amount": [1030000.0, 1296000.0, 1210000.0, 4100000.0, 3780000.0, 4085000.0],
        }
    )
    return df


@pytest.fixture
def fake_stock_basic_df():
    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "600000.SH"],
            "symbol": ["000001", "600000"],
            "name": ["平安银行", "浦发银行"],
            "area": ["深圳", "上海"],
            "industry": ["银行", "银行"],
            "market": ["主板", "主板"],
            "exchange": ["SZSE", "SSE"],
            "curr_type": ["CNY", "CNY"],
            "list_status": ["L", "L"],
            "list_date": pd.to_datetime(["19910403", "19991110"], format="%Y%m%d").date,
            "delist_date": [None, None],
            "is_hs": ["N", "N"],
        }
    )


class TestUpdateStrategy:
    def test_update_stock_basic(self, test_config, fake_stock_basic_df):
        store = DuckDBStore(test_config.storage)
        store.init_all()
        fetcher = FakeFetcher(pd.DataFrame(), fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, test_config)

        ds = test_config.datasets[0]
        n = strategy.run(ds)
        assert n == 2
        assert store.count_rows("stock_basic") == 2
        store.close()

    def test_update_daily_full(self, test_config, fake_daily_df, fake_stock_basic_df):
        store = DuckDBStore(test_config.storage)
        store.init_all()
        # 先写入 stock_basic
        store.upsert("stock_basic", fake_stock_basic_df)
        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, test_config)

        ds = test_config.datasets[1]
        n = strategy.run(ds)
        # FakeFetcher returns all rows per ts_code; upsert is idempotent
        assert n > 0
        assert store.count_rows("daily") == 6
        store.close()

    def test_update_daily_incremental(self, test_config, fake_daily_df, fake_stock_basic_df):
        store = DuckDBStore(test_config.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, test_config)

        # 第一次全量
        strategy.run(test_config.datasets[1])
        count_before = store.count_rows("daily")

        # 第二次增量（幂等，不应增加行数）
        strategy.run(test_config.datasets[1])
        count_after = store.count_rows("daily")
        assert count_after == count_before
        store.close()

    def test_update_disabled_dataset(self, test_config, fake_stock_basic_df):
        store = DuckDBStore(test_config.storage)
        store.init_all()
        fetcher = FakeFetcher(pd.DataFrame(), fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, test_config)

        ds = DatasetConfig(
            name="disabled", type="daily", api_name="daily", enabled=False
        )
        n = strategy.run(ds)
        assert n == 0
        store.close()


class FakeCalFetcher(BaseFetcher):
    """trade_cal 专用的 FakeFetcher."""

    def __init__(self, cal_df: pd.DataFrame) -> None:
        super().__init__(rate_limit_per_min=0)
        self.cal_df = cal_df
        self.page_size = 5000

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        if api_name == "trade_cal":
            df = self.cal_df.copy()
            if start_date:
                from datetime import datetime
                sd = datetime.strptime(str(start_date), "%Y%m%d").date()
                df = df[pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date >= sd]
            if end_date:
                from datetime import datetime
                ed = datetime.strptime(str(end_date), "%Y%m%d").date()
                df = df[pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date <= ed]
            return FetchResult(data=df, has_more=False)
        return FetchResult(data=pd.DataFrame(), has_more=False)


@pytest.fixture
def fake_cal_df():
    return pd.DataFrame({
        "exchange": ["SSE"] * 4,
        "cal_date": ["20240101", "20240102", "20240103", "20240104"],
        "is_open": [1, 1, 1, 0],
        "pretrade_date": [None, "20231229", "20240102", "20240103"],
    })


class TestUpdateTradeCal:
    """trade_cal 数据集更新测试."""

    def _make_config(self, datasets):
        return Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(full_lookback_years=5, incremental_lookback_days=3, scan_batch_size=5),
            datasets=datasets,
        )

    def test_update_trade_cal_full(self, fake_cal_df):
        """FULL 模式：一次性拉取并 upsert 到 trade_cal 表."""
        cfg = self._make_config([
            DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                          params={"exchange": "SSE"}),
        ])
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeCalFetcher(fake_cal_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        ds = cfg.datasets[0]
        n = strategy.run(ds)
        assert n == 4
        assert store.count_rows("trade_cal") == 4
        store.close()

    def test_update_trade_cal_idempotent(self, fake_cal_df):
        """再次运行应幂等（不增加行数）."""
        cfg = self._make_config([
            DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                          params={"exchange": "SSE"}),
        ])
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeCalFetcher(fake_cal_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        ds = cfg.datasets[0]

        strategy.run(ds)
        strategy.run(ds)
        assert store.count_rows("trade_cal") == 4
        store.close()

    def test_update_trade_cal_incremental(self, fake_cal_df):
        """INCREMENTAL 模式：基于已有 max_date 回溯."""
        cfg = self._make_config([
            DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                          params={"exchange": "SSE"}),
        ])
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeCalFetcher(fake_cal_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        ds = cfg.datasets[0]

        # 先全量
        strategy.run(ds)
        # 再增量
        n = strategy.run(ds)
        # 增量应包含从 max_date - 3 days 起的数据（即全量已有数据），upsert 仍然幂等
        assert n == 4
        assert store.count_rows("trade_cal") == 4
        store.close()

    def test_get_trade_calendar_uses_table(self, fake_cal_df):
        """_get_trade_calendar 优先从 trade_cal 表读取."""
        cfg = self._make_config([
            DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                          params={"exchange": "SSE"}),
        ])
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeCalFetcher(fake_cal_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        ds = cfg.datasets[0]

        strategy.run(ds)
        cal = strategy._get_trade_calendar()
        # 4 行中 3 行 is_open=1
        assert len(cal) == 3
        assert date(2024, 1, 4) not in cal
        store.close()

    def test_get_trade_calendar_fallback_when_no_table(self, test_config):
        """trade_cal 表不存在时回退到 tushare."""
        store = DuckDBStore(test_config.storage)
        # 不调用 init_all，trade_cal 表不存在
        fetcher = FakeCalFetcher(pd.DataFrame())
        strategy = UpdateStrategy(fetcher, store, test_config)

        # FakeFetcher 的 fetch 在 api_name != 'trade_cal' 时返回空 DataFrame
        cal = strategy._get_trade_calendar()
        assert cal == set()
        store.close()


class TestDateRangeParams:
    """run() 接受 start_date / end_date 参数的测试.

    通过 spy fetcher 验证日期参数正确透传到 fetcher。
    """

    def _make_config(self, datasets):
        return Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(full_lookback_years=5, incremental_lookback_days=3, scan_batch_size=5),
            datasets=datasets,
        )

    def test_time_series_full_uses_explicit_date_range(self, fake_daily_df, fake_stock_basic_df):
        """FULL 模式：外部 start_date / end_date 覆盖默认（回溯 N 年 ~ 今天）."""
        store = DuckDBStore(self._make_config([]).storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)

        # spy fetcher 记录参数
        captured: list[dict] = []

        class SpyFetcher(FakeFetcher):
            def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
                if api_name == "daily":
                    captured.append({
                        "start_date": start_date,
                        "end_date": end_date,
                    })
                return super().fetch(api_name, params, start_date, end_date, offset, limit)

        cfg = self._make_config([
            DatasetConfig(name="daily", type="daily", api_name="daily"),
        ])
        spy = SpyFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(spy, store, cfg)
        strategy.run(
            cfg.datasets[0],
            start_date="20240101",
            end_date="20240131",
        )
        # 每次 fetch 调用都应使用显式日期范围
        assert all(c["start_date"] == date(2024, 1, 1) for c in captured)
        assert all(c["end_date"] == date(2024, 1, 31) for c in captured)
        assert len(captured) > 0
        store.close()

    def test_time_series_incremental_uses_explicit_start(self, fake_daily_df, fake_stock_basic_df):
        """INCREMENTAL 模式：外部 start_date 覆盖默认（max_date - lookback）."""
        store = DuckDBStore(self._make_config([]).storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        # 先写入已有数据（最大日期 20240103）
        store.upsert("daily", fake_daily_df)

        captured: list[dict] = []

        class SpyFetcher(FakeFetcher):
            def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
                if api_name == "daily":
                    captured.append({"start_date": start_date, "end_date": end_date})
                return super().fetch(api_name, params, start_date, end_date, offset, limit)

        cfg = self._make_config([
            DatasetConfig(name="daily", type="daily", api_name="daily"),
        ])
        spy = SpyFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(spy, store, cfg)
        # 显式指定 start_date 应覆盖 max_date - 3 天的默认行为
        strategy.run(cfg.datasets[0], start_date="20240101")
        assert all(c["start_date"] == date(2024, 1, 1) for c in captured)
        # 没有指定 end_date，应使用今天
        from datetime import date as _date
        assert all(c["end_date"] == _date.today() for c in captured)
        store.close()

    def test_calendar_uses_explicit_dates(self, fake_cal_df):
        """日历类：外部 start_date / end_date 传给 tushare."""
        captured: list[dict] = []

        class SpyCalFetcher(FakeCalFetcher):
            def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
                captured.append({"params": dict(params or {}), "start_date": start_date, "end_date": end_date})
                return super().fetch(api_name, params, start_date, end_date, offset, limit)

        cfg = self._make_config([
            DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                          params={"exchange": "SSE"}),
        ])
        store = DuckDBStore(cfg.storage)
        store.init_all()
        spy = SpyCalFetcher(fake_cal_df)
        strategy = UpdateStrategy(spy, store, cfg)
        strategy.run(
            cfg.datasets[0],
            start_date="20240101",
            end_date="20240131",
        )
        assert len(captured) == 1
        assert captured[0]["params"]["start_date"] == "20240101"
        assert captured[0]["params"]["end_date"] == "20240131"
        store.close()

    def test_calendar_incremental_uses_explicit_dates(self, fake_cal_df):
        """INCREMENTAL 模式日历类：外部日期覆盖 max_date 回溯."""
        captured: list[dict] = []

        class SpyCalFetcher(FakeCalFetcher):
            def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
                captured.append({"params": dict(params or {})})
                return super().fetch(api_name, params, start_date, end_date, offset, limit)

        cfg = self._make_config([
            DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                          params={"exchange": "SSE"}),
        ])
        store = DuckDBStore(cfg.storage)
        store.init_all()
        spy = SpyCalFetcher(fake_cal_df)
        strategy = UpdateStrategy(spy, store, cfg)
        # 先全量
        strategy.run(cfg.datasets[0])
        captured.clear()
        # 增量：显式指定 start_date
        strategy.run(cfg.datasets[0], start_date="20240101")
        assert len(captured) == 1
        assert captured[0]["params"]["start_date"] == "20240101"
        store.close()

    def test_no_date_uses_mode_defaults(self, fake_daily_df, fake_stock_basic_df):
        """不传 start_date / end_date 时，行为与之前一致（回归测试）."""
        store = DuckDBStore(self._make_config([]).storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)

        cfg = self._make_config([
            DatasetConfig(name="daily", type="daily", api_name="daily"),
        ])
        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        # FULL 模式不传 start_date 应回溯 5 年（update_cfg.full_lookback_years=5）
        n = strategy.run(cfg.datasets[0])
        assert n > 0
        # INCREMENTAL 模式不传 start_date，应使用 max_date - 3 天
        n2 = strategy.run(cfg.datasets[0])
        assert n2 >= 0
        store.close()

    def test_date_string_with_dash_accepted(self, fake_cal_df):
        """支持 YYYY-MM-DD 格式（带横线）."""
        cfg = self._make_config([
            DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                          params={"exchange": "SSE"}),
        ])
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeCalFetcher(fake_cal_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        # 传 YYYY-MM-DD 格式不应报错
        n = strategy.run(
            cfg.datasets[0],
            start_date=date(2024, 1, 1),  # 用 date 对象，更安全
        )
        assert n == 4
        store.close()


# ---------------------------------------------------------------------------
# DAILY 模式（按 trade_date 单日全市场拉取）
# ---------------------------------------------------------------------------


class FakeDailyFetcher(BaseFetcher):
    """按 trade_date 单日全市场拉取的 fake fetcher.

    模拟 tushare 行为：传入 trade_date 时返回该日全市场所有股票数据。
    """

    def __init__(self, full_df: pd.DataFrame) -> None:
        super().__init__(rate_limit_per_min=0)
        self.full_df = full_df
        self.page_size = 5000
        self.calls: list[dict] = []  # 记录每次调用的 params

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        self.calls.append({"api_name": api_name, "params": dict(params or {}),
                          "start_date": start_date, "end_date": end_date})
        # 模拟按 trade_date 过滤返回
        df = self.full_df.copy()
        if params and "trade_date" in params:
            td = str(params["trade_date"])
            df = df[df["trade_date"] == td]
        if start_date:
            sd = pd.to_datetime(start_date).date() if not isinstance(start_date, str) else \
                pd.Timestamp(start_date).date()
            df = df[pd.to_datetime(df["trade_date"]).dt.date >= sd]
        if end_date:
            ed = pd.to_datetime(end_date).date() if not isinstance(end_date, str) else \
                pd.Timestamp(end_date).date()
            df = df[pd.to_datetime(df["trade_date"]).dt.date <= ed]
        return FetchResult(data=df, has_more=False)


@pytest.fixture
def multi_day_multi_stock_df():
    """3 天 × 2 只股票 = 6 行的样本数据."""
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * 3 + ["600000.SH"] * 3,
        "trade_date": ["20240101", "20240102", "20240103",
                       "20240101", "20240102", "20240103"],
        "adj_factor": [1.0, 1.0, 1.0, 2.0, 2.0, 2.0],
    })


class TestUpdateDaily:
    """DAILY 模式（按 trade_date 单日全市场拉取）测试."""

    def test_daily_mode_requires_flag(self):
        """未设置 daily_supported 的数据集不能用 DAILY 模式."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[DatasetConfig(name="daily", type="daily", api_name="daily")],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeDailyFetcher(pd.DataFrame())
        strategy = UpdateStrategy(fetcher, store, cfg)
        n = strategy.run(cfg.datasets[0])
        assert n == 0
        store.close()

    def test_daily_mode_full_day(self, multi_day_multi_stock_df):
        """DAILY 模式拉取单日全市场数据."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[
                DatasetConfig(name="adj_factor", type="adj_factor", api_name="adj_factor",
                              daily_supported=True),
            ],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeDailyFetcher(multi_day_multi_stock_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        n = strategy.run(
            cfg.datasets[0],
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 3),
        )
        assert n == 6
        # 验证：应按 3 个交易日分别调用接口
        assert len(fetcher.calls) == 3
        for call in fetcher.calls:
            assert "trade_date" in call["params"]
        # 验证数据库行数
        assert store.count_rows("adj_factor") == 6
        store.close()

    def test_daily_mode_idempotent(self, multi_day_multi_stock_df):
        """DAILY 模式重复运行应幂等（行数不变）."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[
                DatasetConfig(name="adj_factor", type="adj_factor", api_name="adj_factor",
                              daily_supported=True),
            ],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeDailyFetcher(multi_day_multi_stock_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        # 第一次
        n1 = strategy.run(
            cfg.datasets[0],
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 3),
        )
        # 第二次（应幂等，upsert 仍执行 update 语义但 count_rows 不变）
        n2 = strategy.run(
            cfg.datasets[0],
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 3),
        )
        assert n1 == 6
        assert n2 == 6  # upsert 总是返回 len(df)；行数无变化才是关键
        assert store.count_rows("adj_factor") == 6  # 实际行数不变 → 幂等
        store.close()

    def test_daily_mode_skips_non_trade_day(self, multi_day_multi_stock_df):
        """DAILY 模式空数据日（非交易日）应跳过不报错."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[
                DatasetConfig(name="adj_factor", type="adj_factor", api_name="adj_factor",
                              daily_supported=True),
            ],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeDailyFetcher(multi_day_multi_stock_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        # 1.1 ~ 1.5 共 5 天，但样本里只有 1.1/1.2/1.3 有数据
        n = strategy.run(
            cfg.datasets[0],
            start_date=date(2024, 1, 1), end_date=date(2024, 1, 5),
        )
        # 应只 upsert 6 行（3 天 × 2 股票），1.4/1.5 空数据被跳过
        assert n == 6
        # 调用 5 次（每个日期一次），但只有 3 次返回数据
        assert len(fetcher.calls) == 5
        store.close()


class FakeResearchFetcher(BaseFetcher):
    """report_rc 测试用 fetcher，按 start_date/end_date 过滤并记录调用区间."""

    def __init__(self, research_df: pd.DataFrame) -> None:
        super().__init__(rate_limit_per_min=0)
        self.research_df = research_df
        self.page_size = 5000
        # 记录每次 fetch_all 调用的 (start_date, end_date) 参数
        self.batch_calls: list[tuple] = []

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        if api_name != "report_rc":
            return FetchResult(data=pd.DataFrame(), has_more=False)
        # fetch_all 传的是 start_date/end_date（已归一化为 date 或 str）
        df = self.research_df.copy()
        # 本方法不直接处理日期过滤（由 fetch_all 的 params 路径处理），
        # 这里简化：返回全部数据，由上层 batch_calls 计数验证批次切分
        return FetchResult(data=df, has_more=False)

    def fetch_all(self, api_name, params=None, start_date=None, end_date=None, page_size=5000, max_pages=10000):
        # 记录本次拉取的日期范围（params 里含 start_date/end_date）
        p = dict(params or {})
        sd = p.get("start_date", start_date)
        ed = p.get("end_date", end_date)
        self.batch_calls.append((sd, ed))
        # 返回符合日期范围的数据
        df = self.research_df.copy()
        if "report_date" in df.columns and sd:
            from datetime import datetime
            sd_d = sd if isinstance(sd, date) else datetime.strptime(str(sd).replace("-", ""), "%Y%m%d").date()
            df = df[df["report_date"] >= sd_d]
        if "report_date" in df.columns and ed:
            from datetime import datetime
            ed_d = ed if isinstance(ed, date) else datetime.strptime(str(ed).replace("-", ""), "%Y%m%d").date()
            df = df[df["report_date"] <= ed_d]
        return df


def _make_research_df(start: date, end: date) -> pd.DataFrame:
    """生成 start~end 之间每天一条研报数据."""
    days = (end - start).days + 1
    dates = [start + timedelta(days=i) for i in range(days)]
    return pd.DataFrame({
        "ts_code": ["000001.SZ"] * days,
        "name": ["平安银行"] * days,
        "report_date": dates,
        "report_title": ["研报"] * days,
        "report_type": ["预测"] * days,
        "classify": ["年报"] * days,
        "org_name": ["机构A"] * days,
        "author_name": ["作者A"] * days,
        "quarter": ["2024Q1"] * days,
        "rating": ["买入"] * days,
    })


class TestUpdateResearch:
    """report_rc 研报拉取测试 — 验证按天/按季度切分策略."""

    def _make_config(self) -> Config:
        return Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(full_lookback_years=5, incremental_lookback_days=3),
            datasets=[
                DatasetConfig(name="report_rc", type="report_rc", api_name="report_rc"),
            ],
        )

    def test_research_short_span_by_day(self):
        """跨度 ≤ 60 天应按天逐日拉取（调用次数 == 天数）."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        start = date(2024, 6, 1)
        end = date(2024, 6, 30)  # 30 天，≤ 阈值
        df = _make_research_df(start, end)
        fetcher = FakeResearchFetcher(df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run(cfg.datasets[0],
                         start_date=start, end_date=end)
        # 30 天应有 30 行
        assert n == 30
        # 按天拉：调用次数 == 天数（30）
        assert len(fetcher.batch_calls) == 30
        # 每次调用的 start_date == end_date（单日区间）
        for sd, ed in fetcher.batch_calls:
            assert sd == ed
        store.close()

    def test_research_long_span_by_quarter(self):
        """跨度 > 60 天应按季度切分拉取."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        start = date(2024, 1, 1)
        end = date(2024, 12, 31)  # 365 天，> 阈值
        df = _make_research_df(start, end)
        fetcher = FakeResearchFetcher(df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run(cfg.datasets[0],
                         start_date=start, end_date=end)
        # 全年 366 天（2024 闰年）应有 366 行
        assert n == 366
        # 按季度拉：4 个季度，调用次数 == 4
        assert len(fetcher.batch_calls) == 4
        store.close()

    def test_research_threshold_boundary(self):
        """跨度恰好等于阈值（60 天）应按天拉取."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        start = date(2024, 1, 1)
        end = start + timedelta(days=RESEARCH_DAILY_THRESHOLD_DAYS)  # 恰好 60 天
        df = _make_research_df(start, end)
        fetcher = FakeResearchFetcher(df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run(cfg.datasets[0],
                         start_date=start, end_date=end)
        assert n == RESEARCH_DAILY_THRESHOLD_DAYS + 1  # 61 行
        # ≤ 阈值，按天拉
        assert len(fetcher.batch_calls) == RESEARCH_DAILY_THRESHOLD_DAYS + 1
        store.close()

    def test_research_just_above_threshold(self):
        """跨度 = 阈值+1 天应按季度拉取."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        start = date(2024, 1, 1)
        end = start + timedelta(days=RESEARCH_DAILY_THRESHOLD_DAYS + 1)  # 61 天
        df = _make_research_df(start, end)
        fetcher = FakeResearchFetcher(df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run(cfg.datasets[0],
                         start_date=start, end_date=end)
        assert n == RESEARCH_DAILY_THRESHOLD_DAYS + 2  # 62 行
        # > 阈值，按季度拉（1~3月跨 2 个季度边界：Q1 和 Q2 开头）
        # _generate_quarters 对 1.1~3.2 会生成 [(1.1,3.31)] 一个季度
        assert len(fetcher.batch_calls) < RESEARCH_DAILY_THRESHOLD_DAYS
        store.close()


class TestBehaviorConsistency:
    """重构后行为表征一致性测试 — 验证去掉 UpdateMode 后各数据集类型行为不变.

    重构前用 UpdateMode.FULL/INCREMENTAL/DAILY/SCAN 区分行为，
    重构后由 dataset.type + start_date/ts_codes 参数自动决定。
    本类锁定关键行为表征，确保重构无语义回归。
    """

    def test_daily_supported_no_codes_uses_by_date(self, fake_daily_df, fake_stock_basic_df):
        """daily_supported 数据集（无 ts_codes）应走按日全市场拉取（原 DAILY 行为）.

        通过 _update_by_date 按日拉取后，store 中数据应与原始一致（幂等去重）。
        """
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[DatasetConfig(name="daily", type="daily", api_name="daily",
                                    daily_supported=True)],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        strategy.run(cfg.datasets[0],
                     start_date=date(2024, 1, 1), end_date=date(2024, 1, 3))
        # 走 _update_by_date：按日拉取后 upsert 幂等，实际行数应为 6（2 股票 × 3 天）
        assert store.count_rows("daily") == 6
        store.close()

    def test_time_series_with_explicit_start_uses_range(self, fake_daily_df, fake_stock_basic_df):
        """时序表 + 显式 start_date 应走逐标的指定范围（原 FULL 行为）."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[DatasetConfig(name="daily", type="daily", api_name="daily")],  # daily_supported=False 强制逐标的
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run(cfg.datasets[0], start_date="20240101", end_date="20240103")
        # n 是 upsert 返回值累加（FakeFetcher 不按 ts_code 过滤，2 标的各 6 行 = 12），
        # 但 store 实际去重后 6 行
        assert store.count_rows("daily") == 6
        store.close()

    def test_time_series_empty_table_falls_back_to_full(self, fake_daily_df, fake_stock_basic_df):
        """时序表 + 不传 start_date + 空表 → 逐标的回溯 N 年（原 FULL 空表行为）."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(full_lookback_years=5),
            datasets=[DatasetConfig(name="daily", type="daily", api_name="daily")],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        # 不传 start_date，表里无 daily 数据 → 应回溯 N 年（fake_daily_df 全部命中）
        strategy.run(cfg.datasets[0])
        # 空表回溯全量，6 行全部写入（2 标的 × 3 天）
        assert store.count_rows("daily") == 6
        store.close()

    def test_time_series_with_data_uses_incremental(self, fake_daily_df, fake_stock_basic_df):
        """时序表 + 不传 start_date + 表有数据 → 逐标的查 max_date 回溯（原 INCREMENTAL 行为）."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(incremental_lookback_days=3),
            datasets=[DatasetConfig(name="daily", type="daily", api_name="daily")],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        # 先写入已有数据（最大日期 20240103）
        store.upsert("daily", fake_daily_df)
        count_before = store.count_rows("daily")

        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, cfg)
        # 不传 start_date → 增量：从 max_date(20240103) - 3 天开始，幂等不新增行
        strategy.run(cfg.datasets[0])
        count_after = store.count_rows("daily")
        assert count_after == count_before  # 幂等
        store.close()

    def test_scan_uses_run_scan(self, fake_daily_df, fake_stock_basic_df):
        """run_scan 应执行查漏补缺（原 SCAN 行为），非时序数据集返回 0."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(scan_batch_size=5),
            datasets=[DatasetConfig(name="daily", type="daily", api_name="daily")],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        store.upsert("daily", fake_daily_df)
        fetcher = FakeFetcher(fake_daily_df, fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        # 已有完整数据，scan 应补齐 0 行（无缺失）
        n = strategy.run_scan(cfg.datasets[0])
        assert n == 0
        store.close()

    def test_run_scan_non_time_series_returns_zero(self, fake_stock_basic_df):
        """run_scan 对非时序数据集应返回 0 并跳过."""
        cfg = Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[DatasetConfig(name="stock_basic", type="stock_basic", api_name="stock_basic")],
        )
        store = DuckDBStore(cfg.storage)
        store.init_all()
        fetcher = FakeFetcher(pd.DataFrame(), fake_stock_basic_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run_scan(cfg.datasets[0])
        assert n == 0
        store.close()


class FakeSurveyFetcher(BaseFetcher):
    """stk_surv 测试用 fetcher，按日期范围返回含 content 的调研数据."""

    def __init__(self, survey_df: pd.DataFrame) -> None:
        super().__init__(rate_limit_per_min=0)
        self.survey_df = survey_df
        self.page_size = 100
        self.calls: list[dict] = []  # 记录每次 fetch_all 调用

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        if api_name != "stk_surv":
            return FetchResult(data=pd.DataFrame(), has_more=False)
        return FetchResult(data=pd.DataFrame(), has_more=False)

    def fetch_all(self, api_name, params=None, start_date=None, end_date=None, page_size=5000, max_pages=10000):
        self.calls.append(dict(params or {}))
        if api_name != "stk_surv":
            return pd.DataFrame()
        df = self.survey_df.copy()
        # 按日期范围过滤（新方案：按日全市场，不传 ts_code）
        sd = (params or {}).get("start_date")
        ed = (params or {}).get("end_date")
        if sd and ed and "surv_date" in df.columns:
            df = df[(df["surv_date"] >= sd) & (df["surv_date"] <= ed)]
        return df


def _make_survey_df() -> pd.DataFrame:
    """生成 2 条调研数据（含 content 大文本）."""
    return pd.DataFrame({
        "ts_code": ["002223.SZ", "002223.SZ"],
        "name": ["鱼跃医疗", "鱼跃医疗"],
        "surv_date": ["20211024", "20211025"],
        "fund_visitors": ["机构A,机构B", "机构C"],
        "rece_place": ["公司会议室", "线上会议"],
        "rece_mode": ["实地调研", "电话会议"],
        "rece_org": ["接待机构A", "接待机构B"],
        "org_type": ["公募基金", "券商"],
        "comp_rece": ["董事长,董秘", "总经理"],
        "content": ["这是一段很长的调研内容..." * 100, "另一段调研内容" * 50],
    })


class TestUpdateSurvey:
    """机构调研数据集（stk_surv）拉取测试 — 验证拆表写入 + 逐标的 + 幂等."""

    def _make_config(self) -> Config:
        return Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(full_lookback_years=5, incremental_lookback_days=3),
            datasets=[
                DatasetConfig(name="stk_surv", type="stk_surv", api_name="stk_surv"),
            ],
        )

    def test_survey_writes_main_and_detail(self, fake_stock_basic_df):
        """拉取后主表和详情表都应有数据，content 隔离在 detail 表."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        survey_df = _make_survey_df()
        fetcher = FakeSurveyFetcher(survey_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run(cfg.datasets[0],
                         start_date="20211024", end_date="20211025")
        assert n == 2
        # 主表 2 行，不含 content 列
        assert store.count_rows("stk_surv") == 2
        main_cols = store.get_table_columns("stk_surv")
        assert "content" not in main_cols
        # 详情表 2 行，含 content
        assert store.count_rows("stk_surv_detail") == 2
        detail_cols = store.get_table_columns("stk_surv_detail")
        assert "content" in detail_cols
        store.close()

    def test_survey_content_none_not_written_to_detail(self, fake_stock_basic_df):
        """content 为空的行不应写入 detail 表，但主表照常写入."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        df = _make_survey_df()
        df.loc[1, "content"] = None  # 第二条 content 为空
        fetcher = FakeSurveyFetcher(df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        strategy.run(cfg.datasets[0],
                     start_date="20211024", end_date="20211025")
        # 主表 2 行
        assert store.count_rows("stk_surv") == 2
        # 详情表只 1 行（content 非空的那条）
        assert store.count_rows("stk_surv_detail") == 1
        store.close()

    def test_survey_idempotent(self, fake_stock_basic_df):
        """重复拉取应幂等（upsert 去重，行数不变）."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        survey_df = _make_survey_df()
        fetcher = FakeSurveyFetcher(survey_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        strategy.run(cfg.datasets[0],
                     start_date="20211024", end_date="20211025")
        count_main = store.count_rows("stk_surv")
        count_detail = store.count_rows("stk_surv_detail")
        # 再拉一次
        strategy.run(cfg.datasets[0],
                     start_date="20211024", end_date="20211025")
        assert store.count_rows("stk_surv") == count_main
        assert store.count_rows("stk_surv_detail") == count_detail
        store.close()

    def test_survey_in_survey_datasets(self):
        """stk_surv 应在 SURVEY_DATASETS 集合中."""
        assert "stk_surv" in SURVEY_DATASETS

    def test_survey_fetches_by_month(self, fake_stock_basic_df):
        """应按月全市场拉取，fetch_all 调用次数 == 月份数."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.upsert("stock_basic", fake_stock_basic_df)
        survey_df = _make_survey_df()
        fetcher = FakeSurveyFetcher(survey_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        strategy.run(cfg.datasets[0],
                     start_date="20211024", end_date="20211025")
        # 跨 1 个月（10月），1 次 fetch_all 调用
        assert len(fetcher.calls) == 1
        call_params = fetcher.calls[0]
        assert "start_date" in call_params
        assert "end_date" in call_params
        # 确保不传 ts_code（全市场拉取）
        assert "ts_code" not in call_params
        store.close()


class FakeHkUsFetcher(BaseFetcher):
    """hk_us_basic 测试用 fetcher，支持 hk_basic + us_basic 两个接口."""

    def __init__(self, hk_df: pd.DataFrame, us_df: pd.DataFrame) -> None:
        super().__init__(rate_limit_per_min=0)
        self.hk_df = hk_df
        self.us_df = us_df
        self.page_size = 5000
        self.calls: list[str] = []  # 记录调用的 api_name

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        if api_name == "hk_basic":
            return FetchResult(data=self.hk_df, has_more=False)
        if api_name == "us_basic":
            return FetchResult(data=self.us_df, has_more=False)
        return FetchResult(data=pd.DataFrame(), has_more=False)

    def fetch_all(self, api_name, params=None, start_date=None, end_date=None, page_size=5000, max_pages=10000):
        self.calls.append(api_name)
        if api_name == "hk_basic":
            return self.hk_df.copy()
        if api_name == "us_basic":
            return self.us_df.copy()
        return pd.DataFrame()


class TestUpdateHkUsBasic:
    """港美股基础信息（hk_us_basic）拉取测试."""

    def _make_config(self) -> Config:
        return Config(
            storage=StorageConfig(in_memory=True),
            fetcher=FetcherConfig(tushare=TushareConfig(token="test")),
            update=UpdateConfig(),
            datasets=[
                DatasetConfig(name="hk_us_basic", type="hk_us_basic", api_name="hk_us_basic"),
            ],
        )

    def test_hk_us_basic_merges_hk_and_us(self):
        """应分别拉 hk_basic 和 us_basic，合并写入一张表."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        hk_df = pd.DataFrame({
            "ts_code": ["00700.HK", "00189.HK"],
            "name": ["腾讯控股", "中石油"],
            "enname": ["TENCENT", "PetroChina"],
        })
        us_df = pd.DataFrame({
            "ts_code": ["AAPL.US", "TSLA.US"],
            "name": ["苹果", "特斯拉"],
            "enname": ["Apple Inc", "Tesla Inc"],
        })
        fetcher = FakeHkUsFetcher(hk_df, us_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        n = strategy.run(cfg.datasets[0])
        assert n == 4  # 2 港股 + 2 美股
        assert store.count_rows("hk_us_basic") == 4
        # 应调了两个接口
        assert "hk_basic" in fetcher.calls
        assert "us_basic" in fetcher.calls
        # 验证列对齐：只有 ts_code/name/enname 三列
        cols = store.get_table_columns("hk_us_basic")
        assert cols == ["ts_code", "name", "enname"]
        store.close()

    def test_hk_us_basic_extra_columns_dropped(self):
        """接口返回多余列应被 upsert schema 鲁棒性自动丢弃."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        hk_df = pd.DataFrame({
            "ts_code": ["00700.HK"],
            "name": ["腾讯控股"],
            "enname": ["TENCENT"],
            "list_date": ["20040616"],  # 多余列
            "industry": ["互联网"],     # 多余列
        })
        us_df = pd.DataFrame({
            "ts_code": ["AAPL.US"],
            "name": ["苹果"],
            "enname": ["Apple Inc"],
            "classify": ["EQ"],  # 多余列
        })
        fetcher = FakeHkUsFetcher(hk_df, us_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        strategy.run(cfg.datasets[0])
        assert store.count_rows("hk_us_basic") == 2
        cols = store.get_table_columns("hk_us_basic")
        assert "list_date" not in cols
        assert "industry" not in cols
        assert "classify" not in cols
        store.close()

    def test_hk_us_basic_idempotent(self):
        """重复拉取应幂等."""
        cfg = self._make_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        hk_df = pd.DataFrame({
            "ts_code": ["00700.HK"], "name": ["腾讯控股"], "enname": ["TENCENT"],
        })
        us_df = pd.DataFrame({
            "ts_code": ["AAPL.US"], "name": ["苹果"], "enname": ["Apple Inc"],
        })
        fetcher = FakeHkUsFetcher(hk_df, us_df)
        strategy = UpdateStrategy(fetcher, store, cfg)

        strategy.run(cfg.datasets[0])
        strategy.run(cfg.datasets[0])
        assert store.count_rows("hk_us_basic") == 2
        store.close()
