"""pytest 共享 fixtures."""

from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest

from finget.config import Config, StorageConfig, TushareConfig, FetcherConfig, DatasetConfig, UpdateConfig
from finget.storage.duckdb_store import DuckDBStore


# finget 相关的环境变量（test 时清空，避免 .env 干扰）
_FINGET_ENV_VARS = [
    "FINGET_LOG_LEVEL",
    "FINGET_DB_PATH",
    "TUSHARE_TOKEN",
    "TUSHARE_MIRROR_URLS",
    "FINGET_CONFIG_FILE",
    "FINGET_STRATEGY_FILE",  # 旧名，防止开发机残留污染
]


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """每个测试前清空 finget 相关环境变量（避免 .env 文件干扰）.

    单个测试可通过 monkeypatch.setenv() 显式设置需要的变量（如 TUSHARE_TOKEN）。
    同时把 finget.config._PROJECT_ROOT 指向 tmp_path，
    阻止 finget.config._load_env() 加载项目根目录的 .env 文件。
    """
    for key in _FINGET_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    # 让 finget.config 找不到 .env（指向不存在的目录）
    import finget.config as _cfg
    monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
    # 清空 lru_cache，让本次测试重新读 env
    _cfg.get_config.cache_clear()
    yield
    # 测完再清一次，避免污染下一个测试
    _cfg.get_config.cache_clear()


@pytest.fixture
def tmp_config(tmp_path: Path) -> Config:
    """使用内存 DB 的测试配置."""
    return Config(
        storage=StorageConfig(in_memory=True),
        fetcher=FetcherConfig(
            tushare=TushareConfig(token="test_token", mirror_urls=[]),
        ),
        update=UpdateConfig(full_lookback_years=5, incremental_lookback_days=3, scan_batch_size=10),
        datasets=[
            DatasetConfig(name="stock_basic", type="stock_basic", api_name="stock_basic"),
            DatasetConfig(name="daily", type="daily", api_name="daily"),
            DatasetConfig(name="adj_factor", type="adj_factor", api_name="adj_factor"),
        ],
    )


@pytest.fixture
def store(tmp_config: Config) -> DuckDBStore:
    """已初始化的内存 DuckDBStore."""
    s = DuckDBStore(tmp_config.storage)
    s.init_all()
    yield s
    s.close()


@pytest.fixture
def sample_daily_df():
    """样本日线数据."""
    import pandas as pd

    return pd.DataFrame(
        {
            "ts_code": ["000001.SZ", "000001.SZ", "600000.SH"],
            "trade_date": pd.to_datetime(
                ["20240101", "20240102", "20240101"], format="%Y%m%d"
            ).date,
            "open": [10.0, 10.5, 20.0],
            "high": [10.5, 11.0, 21.0],
            "low": [9.8, 10.2, 19.5],
            "close": [10.3, 10.8, 20.5],
            "pre_close": [10.0, 10.3, 20.0],
            "change": [0.3, 0.5, 0.5],
            "pct_chg": [3.0, 4.85, 2.5],
            "vol": [100000.0, 120000.0, 200000.0],
            "amount": [1030000.0, 1296000.0, 4100000.0],
        }
    )


@pytest.fixture
def sample_stock_basic_df():
    import pandas as pd

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
