"""数据获取层测试."""

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from finget.fetchers.base import BaseFetcher, FetchResult
from finget.fetchers.tushare_fetcher import TushareFetcher
from finget.config import TushareConfig


class MockFetcher(BaseFetcher):
    """用于测试的 mock fetcher."""

    def __init__(self, data_pages: list[pd.DataFrame], rate_limit_per_min: int = 0) -> None:
        super().__init__(rate_limit_per_min=rate_limit_per_min)
        self.data_pages = data_pages
        self._call_count = 0

    def fetch(self, api_name, params=None, start_date=None, end_date=None, offset=0, limit=5000):
        if self._call_count < len(self.data_pages):
            df = self.data_pages[self._call_count]
        else:
            df = pd.DataFrame()
        self._call_count += 1
        return FetchResult(data=df, has_more=len(df) >= limit)


class TestBaseFetcher:
    def test_fetch_result_row_count(self):
        df = pd.DataFrame({"a": [1, 2, 3]})
        r = FetchResult(data=df)
        assert r.row_count == 3

    def test_fetch_result_empty(self):
        r = FetchResult(data=pd.DataFrame())
        assert r.row_count == 0

    def test_fetch_all_single_page(self):
        df = pd.DataFrame({"ts_code": ["000001.SZ"], "trade_date": ["20240101"]})
        fetcher = MockFetcher([df])
        result = fetcher.fetch_all("daily")
        assert len(result) == 1

    def test_fetch_all_multi_page(self):
        df1 = pd.DataFrame({"ts_code": ["A"] * 5000, "trade_date": ["20240101"] * 5000})
        df2 = pd.DataFrame({"ts_code": ["B"] * 100, "trade_date": ["20240102"] * 100})
        fetcher = MockFetcher([df1, df2], rate_limit_per_min=0)
        result = fetcher.fetch_all("daily", page_size=5000)
        assert len(result) == 5100

    def test_fetch_all_empty(self):
        fetcher = MockFetcher([])
        result = fetcher.fetch_all("daily")
        assert result.empty


class TestTushareFetcher:
    """TushareFetcher 核心功能测试."""

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_init(self, mock_ts):
        mock_ts.pro_api.return_value = MagicMock()
        cfg = TushareConfig(token="test_token")
        fetcher = TushareFetcher(cfg)
        assert fetcher.config.token == "test_token"
        assert fetcher.page_size == 5000

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_fetch(self, mock_ts):
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api
        mock_api.query.return_value = pd.DataFrame(
            {"ts_code": ["000001.SZ"], "trade_date": ["20240101"], "close": [10.0]}
        )

        cfg = TushareConfig(token="test", rate_limit_per_min=0)
        fetcher = TushareFetcher(cfg)
        result = fetcher.fetch("daily", params={"ts_code": "000001.SZ"}, limit=1)

        assert result.row_count == 1
        assert result.has_more is True  # >= limit

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_fetch_date_format(self, mock_ts):
        """验证日期被正确格式化为 YYYYMMDD."""
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api
        mock_api.query.return_value = pd.DataFrame()

        cfg = TushareConfig(token="test", rate_limit_per_min=0)
        fetcher = TushareFetcher(cfg)
        fetcher.fetch("daily", start_date=date(2024, 1, 1), end_date="2024-01-31")

        call_kwargs = mock_api.query.call_args
        params = call_kwargs.kwargs
        assert params["start_date"] == "20240101"
        assert params["end_date"] == "20240131"

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_get_stock_list(self, mock_ts):
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api
        mock_api.query.return_value = pd.DataFrame(
            {"ts_code": ["000001.SZ", "600000.SH"]}
        )

        cfg = TushareConfig(token="test", rate_limit_per_min=0)
        fetcher = TushareFetcher(cfg)
        codes = fetcher.get_stock_list()
        assert "000001.SZ" in codes
        assert "600000.SH" in codes


class TestMirrorSpeedTest:
    """镜像站测速与 URL 选择测试."""

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_mirror_urls_priority_over_base_url(self, mock_ts):
        """mirror_urls 非空时，应优先测速选择镜像站."""
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api

        # 模拟全部镜像站测速失败 → fallback base_url
        with patch.object(TushareFetcher, "_speed_test", return_value=(None, 0.0)):
            cfg = TushareConfig(
                token="test",
                base_url="https://api.tushare.pro",
                mirror_urls=["https://mirror1.cn", "https://mirror2.cn"],
            )
            fetcher = TushareFetcher(cfg)
            # fallback 到 base_url
            assert fetcher.selected_url == "https://api.tushare.pro"

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_empty_mirror_urls_uses_base_url(self, mock_ts):
        """mirror_urls 为空时，直接使用 base_url."""
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api

        cfg = TushareConfig(
            token="test",
            base_url="https://custom.api.cn",
            mirror_urls=[],
        )
        fetcher = TushareFetcher(cfg)
        assert fetcher.selected_url == "https://custom.api.cn"

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_speed_test_selects_fastest(self, mock_ts):
        """测速应选择响应时间最短的镜像站."""
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api

        # 模拟测速结果：mirror2 更快
        with patch.object(
            TushareFetcher,
            "_speed_test",
            return_value=("https://fast.xiaodefa.cn", 0.3),
        ):
            cfg = TushareConfig(
                token="test",
                mirror_urls=["https://tt.xiaodefa.cn", "https://fast.xiaodefa.cn"],
            )
            fetcher = TushareFetcher(cfg)
            assert fetcher.selected_url == "https://fast.xiaodefa.cn"

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_http_url_set_on_pro_api(self, mock_ts):
        """验证 pro_api._DataApi__http_url 被正确设置."""
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api

        with patch.object(
            TushareFetcher,
            "_speed_test",
            return_value=("https://fast.xiaodefa.cn", 0.3),
        ):
            cfg = TushareConfig(
                token="test",
                mirror_urls=["https://fast.xiaodefa.cn"],
            )
            fetcher = TushareFetcher(cfg)
            # 验证 __http_url 属性被设置
            assert mock_api._DataApi__http_url == "https://fast.xiaodefa.cn"

    def test_http_request_success(self):
        """_http_request 正常响应时返回响应时间."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("finget.fetchers.tushare_fetcher.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.request.return_value = mock_response

            cfg = TushareConfig(token="test", speed_test_timeout=5.0)
            # 直接测试 _http_request（不创建 fetcher，避免 pro_api 依赖）
            from finget.fetchers.tushare_fetcher import TushareFetcher

            # 用 mock 替代 ts.pro_api
            with patch("finget.fetchers.tushare_fetcher.ts"):
                fetcher = TushareFetcher.__new__(TushareFetcher)
                fetcher.config = cfg
                elapsed = fetcher._http_request("https://test.cn")
                assert elapsed is not None
                assert elapsed >= 0

    def test_http_request_timeout(self):
        """_http_request 超时时返回 None."""
        import httpx

        with patch("finget.fetchers.tushare_fetcher.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.request.side_effect = httpx.TimeoutException("timeout")

            cfg = TushareConfig(token="test", speed_test_timeout=1.0)
            from finget.fetchers.tushare_fetcher import TushareFetcher

            with patch("finget.fetchers.tushare_fetcher.ts"):
                fetcher = TushareFetcher.__new__(TushareFetcher)
                fetcher.config = cfg
                result = fetcher._http_request("https://slow.cn")
                assert result is None

    def test_http_request_connect_error(self):
        """_http_request 连接错误时返回 None."""
        import httpx

        with patch("finget.fetchers.tushare_fetcher.httpx.Client") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)
            mock_client.request.side_effect = httpx.ConnectError("refused")

            cfg = TushareConfig(token="test")
            from finget.fetchers.tushare_fetcher import TushareFetcher

            with patch("finget.fetchers.tushare_fetcher.ts"):
                fetcher = TushareFetcher.__new__(TushareFetcher)
                fetcher.config = cfg
                result = fetcher._http_request("https://dead.cn")
                assert result is None

    def test_ssl_error_http_fallback(self):
        """HTTPS SSL 错误时应自动降级 HTTP 重试."""
        import httpx

        cfg = TushareConfig(token="test", mirror_urls=["https://ssl-broken.cn"])
        from finget.fetchers.tushare_fetcher import TushareFetcher

        with patch("finget.fetchers.tushare_fetcher.ts"):
            fetcher = TushareFetcher.__new__(TushareFetcher)
            fetcher.config = cfg

            # HTTPS 失败，HTTP 成功
            with patch.object(fetcher, "_http_request") as mock_http:
                # 第一次调用(HTTPS)返回 None，第二次(HTTP降级)返回 0.5s
                mock_http.side_effect = [None, 0.5]
                result = fetcher._test_single_url("https://ssl-broken.cn")
                assert result == 0.5
                # 验证调用了两次：HTTPS + HTTP fallback
                assert mock_http.call_count == 2


class TestProBar:
    """ts.pro_bar() 模块级函数测试."""

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_pro_bar_passes_api_param(self, mock_ts):
        """pro_bar 必须传 api=self._api 才能使用镜像站."""
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api

        cfg = TushareConfig(token="test", rate_limit_per_min=0)
        with patch.object(TushareFetcher, "_select_best_url", return_value=("https://mirror.cn", 0.0)):
            fetcher = TushareFetcher(cfg)

        # 模拟 ts.pro_bar 返回数据
        expected_df = pd.DataFrame({
            "ts_code": ["002594.SZ"],
            "trade_date": ["20180101"],
            "close": [10.0],
        })

        # patch ts.pro_bar（模块级函数），注意 mock_ts 已经 patch 了整个 ts 模块
        mock_ts.pro_bar.return_value = expected_df
        result = fetcher.pro_bar(
            ts_code="002594.SZ",
            start_date="20180101",
            end_date="20181011",
            adj="qfq",
        )

        assert len(result) == 1
        # 关键验证：api 参数必须传入
        call_kwargs = mock_ts.pro_bar.call_args.kwargs
        assert call_kwargs["api"] == mock_api
        assert call_kwargs["ts_code"] == "002594.SZ"
        assert call_kwargs["adj"] == "qfq"

    @patch("finget.fetchers.tushare_fetcher.ts")
    def test_pro_bar_returns_empty_on_none(self, mock_ts):
        """ts.pro_bar 返回 None 时应返回空 DataFrame."""
        mock_api = MagicMock()
        mock_ts.pro_api.return_value = mock_api

        cfg = TushareConfig(token="test", rate_limit_per_min=0)
        with patch.object(TushareFetcher, "_select_best_url", return_value=("https://mirror.cn", 0.0)):
            fetcher = TushareFetcher(cfg)

        mock_ts.pro_bar.return_value = None
        result = fetcher.pro_bar(ts_code="002594.SZ")
        assert result.empty
