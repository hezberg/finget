"""CLI 入口测试.

使用 click.testing.CliRunner 模拟命令行调用，
不实际连接 tushare（mock fetcher）。

命令结构:
  finget init               一站式初始化
  finget init --schema-only 仅建表结构（不拉数据）
  finget fetch <ds> -S -E   拉取数据集（不传日期默认往前1年）
  finget fetch latest       按策略配置每日增量
  finget scan               按策略配置查漏补缺
  finget stats              数据统计
  finget show <table>       查看表内容
  finget db recreate        删表重建
"""

from __future__ import annotations

from click.testing import CliRunner
from datetime import date

import pandas as pd

from finget.cli import main
from finget.config import (
    Config,
    DatasetConfig,
    FetcherConfig,
    StorageConfig,
    StrategyConfig,
    TushareConfig,
    UpdateConfig,
)
from finget.fetchers.base import BaseFetcher, FetchResult


# =========================================================================
# fetch 命令测试
# =========================================================================


class _RecordingFetcher(BaseFetcher):
    """记录每次 fetch_all 调用的入参."""

    def __init__(self) -> None:
        super().__init__(rate_limit_per_min=0)
        self.page_size = 5000
        self.calls: list[dict] = []

    def fetch(
        self,
        api_name: str,
        params: dict | None = None,
        start_date=None,
        end_date=None,
        offset: int = 0,
        limit: int = 5000,
    ) -> FetchResult:
        if api_name == "stock_basic":
            df = pd.DataFrame({
                "ts_code": ["000001.SZ"],
                "symbol": ["000001"],
                "name": ["平安银行"],
                "list_date": [date(1991, 4, 3)],
            })
        elif api_name == "trade_cal":
            df = pd.DataFrame({
                "exchange": ["SSE"],
                "cal_date": ["20240101"],
                "is_open": [1],
                "pretrade_date": [None],
            })
        else:
            df = pd.DataFrame({
                "ts_code": ["000001.SZ"],
                "trade_date": [date(2024, 1, 1)],
            })
        return FetchResult(data=df, has_more=False)

    def fetch_all(self, api_name, params=None, start_date=None, end_date=None, page_size=5000, max_pages=10000):
        self.calls.append({
            "api_name": api_name,
            "params": dict(params or {}),
            "start_date": start_date,
            "end_date": end_date,
        })
        return super().fetch_all(
            api_name, params, start_date, end_date, page_size, max_pages
        )


def _make_stub(recorder: _RecordingFetcher):
    """构造一个替换 TushareFetcher 的 stub 类."""
    class _StubTushareFetcher:
        def __init__(self, cfg) -> None:
            self.cfg = cfg
            self.fetch = recorder.fetch
            self.fetch_all = recorder.fetch_all
            self.page_size = 5000
            self.selected_url = "https://test.cn"
    return _StubTushareFetcher


class TestFetchCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_fetch_help(self):
        """fetch --help 应显示选项和数据集说明."""
        result = self.runner.invoke(main, ["fetch", "--help"])
        assert result.exit_code == 0
        assert "--start-date" in result.output
        assert "--end-date" in result.output
        assert "daily" in result.output

    def test_fetch_no_dataset_shows_help(self, tmp_path, monkeypatch):
        """不传数据集名应显示帮助."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        from finget.config import get_config
        get_config.cache_clear()

        result = self.runner.invoke(main, ["fetch"])
        assert result.exit_code != 0

    def test_fetch_no_config_no_token_errors(self, tmp_path, monkeypatch):
        """无 TUSHARE_TOKEN 应报错并提示创建 .env."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        result = self.runner.invoke(main, ["fetch", "trade_cal"])
        assert result.exit_code != 0
        assert "TUSHARE_TOKEN not found" in result.output
        assert ".env" in result.output

    def test_fetch_unknown_dataset(self, tmp_path, monkeypatch):
        """未知数据集应报错并提示可用数据集."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        from finget.config import get_config
        get_config.cache_clear()
        result = self.runner.invoke(main, ["fetch", "unknown_ds"])
        assert result.exit_code != 0
        assert "未知数据集" in result.output
        assert "daily" in result.output  # 应提示可用数据集

    def test_fetch_invalid_date_format(self, tmp_path, monkeypatch):
        """无效的日期格式应报错并退出非 0."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        result = self.runner.invoke(
            main, ["fetch", "daily",
                   "--start-date", "2024-01-99"]
        )
        assert result.exit_code != 0
        assert "Invalid start_date" in result.output

    def test_fetch_passes_dates_to_strategy(self, tmp_path, monkeypatch):
        """CLI 应将 --start-date / --end-date 透传到 UpdateStrategy.run()."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        import finget.fetchers.tushare_fetcher as tf_mod

        recorder = _RecordingFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(recorder))

        result = self.runner.invoke(
            main, ["fetch", "trade_cal",
                   "--start-date", "20240101",
                   "--end-date", "20240131"]
        )
        assert result.exit_code == 0, result.output
        assert len(recorder.calls) == 1
        call = recorder.calls[0]
        assert call["api_name"] == "trade_cal"
        assert call["params"]["start_date"] == "20240101"
        assert call["params"]["end_date"] == "20240131"

    def test_fetch_date_with_dash_accepted(self, tmp_path, monkeypatch):
        """--start-date 接受 YYYY-MM-DD 格式（自动去除横线）."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        import finget.fetchers.tushare_fetcher as tf_mod

        recorder = _RecordingFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(recorder))

        result = self.runner.invoke(
            main, ["fetch", "trade_cal",
                   "--start-date", "2024-01-01",
                   "--end-date", "2024-01-31"]
        )
        assert result.exit_code == 0, result.output
        assert len(recorder.calls) == 1
        assert recorder.calls[0]["params"]["start_date"] == "20240101"
        assert recorder.calls[0]["params"]["end_date"] == "20240131"

    def test_fetch_short_options_S_E(self, tmp_path, monkeypatch):
        """短选项 -S / -E 应正常工作."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        import finget.fetchers.tushare_fetcher as tf_mod

        recorder = _RecordingFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(recorder))

        result = self.runner.invoke(
            main, ["fetch", "trade_cal",
                   "-S", "20240101", "-E", "20240131"]
        )
        assert result.exit_code == 0, result.output
        assert recorder.calls[0]["params"]["start_date"] == "20240101"
        assert recorder.calls[0]["params"]["end_date"] == "20240131"

    def test_fetch_default_start_date(self, tmp_path, monkeypatch):
        """不传 --start-date 时应自动增量（用 weekly + codes 验证）."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        import finget.fetchers.tushare_fetcher as tf_mod

        recorder = _RecordingFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(recorder))

        result = self.runner.invoke(
            main, ["fetch", "weekly", "-s", "000001.SZ"]
        )
        assert result.exit_code == 0, result.output
        assert "自动增量" in result.output

    def test_fetch_stock_basic_no_date(self, tmp_path, monkeypatch):
        """stock_basic 不需要日期参数，应正常运行."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        import finget.fetchers.tushare_fetcher as tf_mod

        recorder = _RecordingFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(recorder))

        result = self.runner.invoke(main, ["fetch", "stock_basic"])
        assert result.exit_code == 0, result.output

    def test_fetch_daily_uses_daily_mode(self, tmp_path, monkeypatch):
        """daily 数据集应自动使用 DAILY 模式."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        import finget.fetchers.tushare_fetcher as tf_mod

        recorder = _RecordingFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(recorder))

        result = self.runner.invoke(main, ["fetch", "daily"])
        assert result.exit_code == 0, result.output
        assert "DAILY" in result.output or "daily" in result.output

    def test_fetch_stk_surv_by_ts_code(self, tmp_path, monkeypatch):
        """stk_surv 应逐标的拉取（指定 --codes 时走逐标的）."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        import finget.fetchers.tushare_fetcher as tf_mod

        class _SurvFetcher(BaseFetcher):
            def __init__(self) -> None:
                super().__init__(rate_limit_per_min=0)
                self.page_size = 100
                self.calls: list[dict] = []

            def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
                if api_name == "stock_basic":
                    return FetchResult(data=pd.DataFrame({
                        "ts_code": ["000001.SZ"], "name": ["平安银行"],
                        "list_date": [date(1991, 4, 3)],
                    }), has_more=False)
                if api_name == "stk_surv":
                    return FetchResult(data=pd.DataFrame({
                        "ts_code": ["000001.SZ"],
                        "name": ["平安银行"],
                        "surv_date": ["20240101"],
                        "fund_visitors": ["机构A"],
                        "rece_place": ["会议室"],
                        "rece_mode": ["实地"],
                        "rece_org": ["机构A"],
                        "org_type": ["公募"],
                        "comp_rece": ["董秘"],
                        "content": ["调研内容"],
                    }), has_more=False)
                return FetchResult(data=pd.DataFrame(), has_more=False)

            def fetch_all(self, api_name, params=None, start_date=None, end_date=None, page_size=5000, max_pages=10000):
                self.calls.append({
                    "api_name": api_name,
                    "params": dict(params or {}),
                })
                result = self.fetch(api_name, params, start_date, end_date, 0, page_size)
                if result.data.empty:
                    return pd.DataFrame()
                return result.data

        surv_fetcher = _SurvFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(surv_fetcher))

        result = self.runner.invoke(
            main, ["fetch", "stk_surv", "-s", "000001.SZ", "-S", "20240101"]
        )
        assert result.exit_code == 0, result.output
        # 应调用 stk_surv 接口
        surv_calls = [c for c in surv_fetcher.calls if c["api_name"] == "stk_surv"]
        assert len(surv_calls) >= 1


# =========================================================================
# fetch latest 测试
# =========================================================================


class TestFetchLatestCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_fetch_latest_help(self):
        """fetch --help 应包含 latest 说明."""
        result = self.runner.invoke(main, ["fetch", "--help"])
        assert result.exit_code == 0
        assert "latest" in result.output

    def test_fetch_latest_no_token_errors(self, tmp_path, monkeypatch):
        """无 TUSHARE_TOKEN 应报错."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        from finget.config import get_config
        get_config.cache_clear()
        result = self.runner.invoke(main, ["fetch", "latest"])
        assert result.exit_code != 0
        assert "TUSHARE_TOKEN not found" in result.output

    def test_fetch_latest_runs(self, tmp_path, monkeypatch):
        """fetch latest 应按策略配置运行."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_DB_PATH", str(tmp_path / "test.duckdb"))
        from finget.config import get_config
        get_config.cache_clear()

        import finget.fetchers.tushare_fetcher as tf_mod
        recorder = _RecordingFetcher()
        monkeypatch.setattr(tf_mod, "TushareFetcher", _make_stub(recorder))

        # 先建表，让 latest 有表可写
        from finget.storage.duckdb_store import DuckDBStore
        cfg = get_config()
        store = DuckDBStore(cfg.storage)
        store.init_all()
        store.close()

        result = self.runner.invoke(main, ["fetch", "latest"])
        assert result.exit_code == 0, result.output
        assert "latest" in result.output


# =========================================================================
# scan 命令测试
# =========================================================================


class TestScanCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_scan_help(self):
        """scan --help 应显示说明."""
        result = self.runner.invoke(main, ["scan", "--help"])
        assert result.exit_code == 0
        assert "策略" in result.output or "scan" in result.output

    def test_scan_no_token_errors(self, tmp_path, monkeypatch):
        """无 TUSHARE_TOKEN 应报错."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        from finget.config import get_config
        get_config.cache_clear()
        result = self.runner.invoke(main, ["scan"])
        assert result.exit_code != 0
        assert "TUSHARE_TOKEN not found" in result.output


# =========================================================================
# init 命令测试
# =========================================================================


class _InitRecordingFetcher(BaseFetcher):
    """init 命令专用 fetcher，根据 api_name 返回模拟数据."""

    def __init__(self) -> None:
        super().__init__(rate_limit_per_min=0)
        self.page_size = 5000
        self.calls: list[dict] = []

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        if api_name == "stock_basic":
            df = pd.DataFrame({
                "ts_code": ["000001.SZ", "600000.SH", "002594.SZ"],
                "symbol": ["000001", "600000", "002594"],
                "name": ["平安银行", "浦发银行", "比亚迪"],
                "area": ["深圳", "上海", "广东"],
                "industry": ["银行", "银行", "汽车"],
                "market": ["主板", "主板", "主板"],
                "exchange": ["SZSE", "SSE", "SZSE"],
                "curr_type": ["CNY", "CNY", "CNY"],
                "list_status": ["L", "L", "L"],
                "list_date": [date(1991, 4, 3), date(1999, 11, 10), date(2011, 6, 30)],
                "delist_date": [None, None, None],
                "is_hs": ["N", "N", "H"],
            })
        elif api_name == "trade_cal":
            ex = (params or {}).get("exchange", "SSE")
            df = pd.DataFrame({
                "exchange": [ex, ex, ex],
                "cal_date": ["20240101", "20240102", "20240103"],
                "is_open": [1, 1, 0],
                "pretrade_date": [None, "20231229", "20240102"],
            })
        else:
            df = pd.DataFrame()
        return FetchResult(data=df, has_more=False)

    def fetch_all(self, api_name, params=None, start_date=None, end_date=None, page_size=5000, max_pages=10000):
        result = self.fetch(api_name, params, start_date, end_date, 0, page_size)
        return result.data


def _make_init_stub(recorder: _InitRecordingFetcher):
    class _Stub:
        def __init__(self, cfg) -> None:
            self.cfg = cfg
            self.fetch = recorder.fetch
            self.fetch_all = recorder.fetch_all
            self.page_size = 5000
            self.selected_url = "https://test.cn"
    return _Stub


class TestInitCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def _setup_token_and_fetcher(self, tmp_path, monkeypatch, recorder=None):
        """公共 fixture：设置 TUSHARE_TOKEN + mock fetcher + 隔离 .env."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        test_db_dir = tmp_path / "init_test"
        test_db_dir.mkdir(exist_ok=True)
        db_path = str(test_db_dir / "init.duckdb")
        monkeypatch.setenv("FINGET_DB_PATH", db_path)
        from finget.config import get_config
        get_config.cache_clear()
        if recorder is not None:
            import finget.fetchers.tushare_fetcher as tf_mod
            monkeypatch.setattr(tf_mod, "TushareFetcher", _make_init_stub(recorder))

    def test_init_help(self):
        """init --help 应显示选项."""
        result = self.runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--exchanges" in result.output
        assert "--skip-stock-basic" in result.output
        assert "--skip-trade-cal" in result.output
        assert "--list-status" in result.output

    def test_init_no_date_options(self):
        """init 不应有 --start-date / --end-date 选项."""
        result = self.runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--start-date" not in result.output
        assert "--end-date" not in result.output

    def test_init_no_token_friendly_error(self, tmp_path, monkeypatch):
        """无 TUSHARE_TOKEN 时 init 应提示创建 .env."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        from finget.config import get_config
        get_config.cache_clear()
        result = self.runner.invoke(main, ["init"])
        assert result.exit_code != 0
        assert "TUSHARE_TOKEN not found" in result.output

    def test_init_skip_both_errors(self, tmp_path, monkeypatch):
        """同时 skip stock_basic 和 trade_cal 应报错."""
        self._setup_token_and_fetcher(tmp_path, monkeypatch)
        result = self.runner.invoke(
            main, ["init",
                   "--skip-stock-basic", "--skip-trade-cal"]
        )
        assert result.exit_code != 0
        assert "不能同时跳过" in result.output

    def test_init_full_runs_both(self, tmp_path, monkeypatch):
        """默认 init 应建表 + 拉 stock_basic + 拉 SSE+SZSE trade_cal."""
        recorder = _InitRecordingFetcher()
        self._setup_token_and_fetcher(tmp_path, monkeypatch, recorder)

        result = self.runner.invoke(main, ["init"])
        assert result.exit_code == 0, result.output

        from finget.config import get_config
        cfg = get_config()
        from finget.storage.duckdb_store import DuckDBStore
        store = DuckDBStore(cfg.storage)
        try:
            assert store.count_rows("stock_basic") == 3
            assert store.count_rows("trade_cal") == 6
            sse_count = store.query("SELECT COUNT(*) AS c FROM trade_cal WHERE exchange='SSE'").iloc[0]["c"]
            szse_count = store.query("SELECT COUNT(*) AS c FROM trade_cal WHERE exchange='SZSE'").iloc[0]["c"]
            assert sse_count == 3
            assert szse_count == 3
        finally:
            store.close()

    def test_init_custom_exchanges(self, tmp_path, monkeypatch):
        """--exchanges 可自定义交易所列表."""
        recorder = _InitRecordingFetcher()
        self._setup_token_and_fetcher(tmp_path, monkeypatch, recorder)

        result = self.runner.invoke(
            main, ["init",
                   "--exchanges", "SSE", "--skip-stock-basic"]
        )
        assert result.exit_code == 0, result.output
        from finget.config import get_config
        cfg = get_config()
        from finget.storage.duckdb_store import DuckDBStore
        store = DuckDBStore(cfg.storage)
        try:
            sse_count = store.query("SELECT COUNT(*) AS c FROM trade_cal WHERE exchange='SSE'").iloc[0]["c"]
            szse_count = store.query("SELECT COUNT(*) AS c FROM trade_cal WHERE exchange='SZSE'").iloc[0]["c"]
            assert sse_count == 3
            assert szse_count == 0
        finally:
            store.close()

    def test_init_skip_stock_basic(self, tmp_path, monkeypatch):
        """--skip-stock-basic 应只拉 trade_cal."""
        recorder = _InitRecordingFetcher()
        self._setup_token_and_fetcher(tmp_path, monkeypatch, recorder)

        result = self.runner.invoke(main, ["init", "--skip-stock-basic"])
        assert result.exit_code == 0, result.output
        from finget.config import get_config
        cfg = get_config()
        from finget.storage.duckdb_store import DuckDBStore
        store = DuckDBStore(cfg.storage)
        try:
            assert store.count_rows("stock_basic") == 0
            assert store.count_rows("trade_cal") == 6
        finally:
            store.close()

    def test_init_skip_trade_cal(self, tmp_path, monkeypatch):
        """--skip-trade-cal 应只拉 stock_basic."""
        recorder = _InitRecordingFetcher()
        self._setup_token_and_fetcher(tmp_path, monkeypatch, recorder)

        result = self.runner.invoke(main, ["init", "--skip-trade-cal"])
        assert result.exit_code == 0, result.output
        from finget.config import get_config
        cfg = get_config()
        from finget.storage.duckdb_store import DuckDBStore
        store = DuckDBStore(cfg.storage)
        try:
            assert store.count_rows("stock_basic") == 3
            assert store.count_rows("trade_cal") == 0
        finally:
            store.close()

    def test_init_with_list_status(self, tmp_path, monkeypatch):
        """--list-status 参数应传给 stock_basic."""
        recorder = _InitRecordingFetcher()
        self._setup_token_and_fetcher(tmp_path, monkeypatch, recorder)

        result = self.runner.invoke(
            main, ["init", "--skip-trade-cal", "--list-status", "D"]
        )
        assert result.exit_code == 0, result.output
        from finget.config import get_config
        cfg = get_config()
        from finget.storage.duckdb_store import DuckDBStore
        store = DuckDBStore(cfg.storage)
        try:
            assert store.count_rows("stock_basic") == 3
        finally:
            store.close()


# =========================================================================
# db 子组命令测试
# =========================================================================


class TestDbCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_db_help(self):
        """db --help 应显示 recreate 子命令（init 已合并进 init --schema-only）."""
        result = self.runner.invoke(main, ["db", "--help"])
        assert result.exit_code == 0
        assert "recreate" in result.output

    def test_init_schema_only_creates_tables(self, tmp_path, monkeypatch):
        """init --schema-only 应创建所有表（不拉数据，不需要 TUSHARE_TOKEN）."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        # 故意不设 TUSHARE_TOKEN，验证 schema-only 不依赖 token
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        test_db_dir = tmp_path / "schema_only_test"
        test_db_dir.mkdir(exist_ok=True)
        db_path = str(test_db_dir / "test.duckdb")
        monkeypatch.setenv("FINGET_DB_PATH", db_path)

        result = self.runner.invoke(main, ["init", "--schema-only"])
        assert result.exit_code == 0, result.output
        assert "已创建" in result.output

    def test_init_schema_only_recreate(self, tmp_path, monkeypatch):
        """init --schema-only --recreate 应删表重建且不拉数据."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
        test_db_dir = tmp_path / "schema_only_recreate_test"
        test_db_dir.mkdir(exist_ok=True)
        db_path = str(test_db_dir / "test.duckdb")
        monkeypatch.setenv("FINGET_DB_PATH", db_path)

        result = self.runner.invoke(main, ["init", "--schema-only", "--recreate"])
        assert result.exit_code == 0, result.output
        assert "已创建" in result.output

    def test_db_recreate_without_confirm_warns(self, tmp_path, monkeypatch):
        """db recreate 不传 --confirm 应提示警告并退出."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        test_db_dir = tmp_path / "db_recreate_test"
        test_db_dir.mkdir(exist_ok=True)
        db_path = str(test_db_dir / "test.duckdb")
        monkeypatch.setenv("FINGET_DB_PATH", db_path)
        from finget.config import get_config
        get_config.cache_clear()

        # 先建表，让 recreate 有表可删
        result = self.runner.invoke(main, ["init", "--schema-only"])
        assert result.exit_code == 0

        # recreate 不传 --confirm，应提示警告
        result = self.runner.invoke(main, ["db", "recreate"])
        assert result.exit_code != 0
        assert "--confirm" in result.output

    def test_db_recreate_with_confirm(self, tmp_path, monkeypatch):
        """db recreate --confirm 应重建所有表."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        test_db_dir = tmp_path / "db_recreate_confirm_test"
        test_db_dir.mkdir(exist_ok=True)
        db_path = str(test_db_dir / "test.duckdb")
        monkeypatch.setenv("FINGET_DB_PATH", db_path)
        from finget.config import get_config
        get_config.cache_clear()

        result = self.runner.invoke(main, ["db", "recreate", "--confirm"])
        assert result.exit_code == 0, result.output
        assert "已重建" in result.output


# =========================================================================
# stats / show 顶级命令测试
# =========================================================================


class TestStatsCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_stats_help(self):
        """stats --help 应可用."""
        result = self.runner.invoke(main, ["stats", "--help"])
        assert result.exit_code == 0

    def test_stats_empty_db(self, tmp_path, monkeypatch):
        """stats 在空数据库上应正常执行."""
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        test_db_dir = tmp_path / "stats_test"
        test_db_dir.mkdir(exist_ok=True)
        db_path = str(test_db_dir / "stats.duckdb")
        monkeypatch.setenv("FINGET_DB_PATH", db_path)
        from finget.config import get_config
        get_config.cache_clear()

        result = self.runner.invoke(main, ["stats"])
        assert result.exit_code == 0


class TestShowCommand:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_show_help(self):
        """show --help 应显示选项."""
        result = self.runner.invoke(main, ["show", "--help"])
        assert result.exit_code == 0
        assert "--output" in result.output
        assert "--limit" in result.output


# =========================================================================
# 顶级 help 测试
# =========================================================================


class TestMainHelp:
    def setup_method(self) -> None:
        self.runner = CliRunner()

    def test_main_help(self):
        """finget --help 应显示所有命令."""
        result = self.runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "fetch" in result.output
        assert "scan" in result.output
        assert "stats" in result.output
        assert "show" in result.output
        assert "db" in result.output
