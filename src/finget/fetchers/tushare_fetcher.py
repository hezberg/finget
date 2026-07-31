"""tushare 数据源适配器（支持镜像站自动测速选择）."""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Any

import httpx
import pandas as pd
import tushare as ts

from finget.config import TushareConfig
from finget.fetchers.base import BaseFetcher, FetchResult
from finget.logging import log


class TushareFetcher(BaseFetcher):
    """tushare 数据源适配器.

    通过 tushare SDK 获取 K 线、指标、公司基础信息等金融数据。
    支持镜像站自动测速选择和分页拉取。

    镜像站机制：
        1. 若配置了 mirror_urls（非空列表），初始化时自动测速选最快的
        2. 若未配置或为空列表，使用 base_url（单一地址）
        3. 选定的 URL 通过 pro._DataApi__http_url 设置到 pro_api 实例
        4. ts.pro_bar() 等模块级函数需传 api=pro 参数
    """

    def __init__(self, config: TushareConfig) -> None:
        super().__init__(rate_limit_per_min=config.rate_limit_per_min)
        self.config = config
        self.page_size = config.page_size

        # --- 镜像站测速 & URL 选择 ---
        self._selected_url, self._selected_time = self._select_best_url()

        # --- 初始化 pro_api ---
        self._api = ts.pro_api(config.token)
        # 设置镜像站地址（关键步骤）
        self._api._DataApi__http_url = self._selected_url

        log.info(
            f"TushareFetcher ready: url={self._selected_url}, "
            f"rate_limit={config.rate_limit_per_min}/min"
        )

    # ------------------------------------------------------------------
    # 镜像站测速
    # ------------------------------------------------------------------

    def _select_best_url(self) -> tuple[str, float]:
        """从配置的镜像站中测速选择最快的一个.

        Returns:
            (url, response_time_seconds) 元组.

        若 mirror_urls 为空列表，直接使用 base_url（时间记 0）。
        测速方法：对每个 URL 发送轻量 HTTP HEAD 请求，
        选响应时间最短且成功的地址。
        若全部超时/失败，fallback 到 base_url。
        """
        mirror_urls = self.config.mirror_urls

        if not mirror_urls:
            log.debug(f"No mirror_urls configured, using base_url: {self.config.base_url}")
            return self.config.base_url, 0.0

        log.debug(f"Speed testing {len(mirror_urls)} mirror URLs...")
        best_url, best_time = self._speed_test(mirror_urls)

        if best_url:
            log.debug(f"Selected mirror: {best_url} (response time: {best_time:.2f}s)")
            return best_url, best_time

        # 全部失败，fallback
        log.warning(f"All mirrors failed, fallback to base_url: {self.config.base_url}")
        return self.config.base_url, 0.0

    def _speed_test(self, urls: list[str]) -> tuple[str | None, float]:
        """对一组 URL 进行测速，返回最快成功的 (url, elapsed_seconds).

        测速策略：
            1. 先尝试 HTTPS 版本（更安全稳定）
            2. 若 HTTPS 出 SSL 错误，自动降级为 HTTP 重试
            3. 取响应时间最短的成功 URL
        """
        results: list[tuple[str, float]] = []

        for url in urls:
            elapsed = self._test_single_url(url)
            if elapsed is not None:
                results.append((url, elapsed))

        if not results:
            return None, 0.0

        # 选最快
        results.sort(key=lambda x: x[1])
        return results[0]

    def _test_single_url(self, url: str) -> float | None:
        """测试单个 URL 的响应时间.

        优先 HTTPS，若 SSL 错误则降级 HTTP。
        返回响应时间（秒），失败返回 None。
        """
        # 先试 HTTPS
        elapsed = self._http_request(url, method="HEAD")
        if elapsed is not None:
            return elapsed

        # HTTPS 失败，尝试 HTTP 降级
        http_url = url.replace("https://", "http://")
        if http_url != url:
            log.warning(f"HTTPS failed for {url}, trying HTTP fallback: {http_url}")
            elapsed = self._http_request(http_url, method="HEAD")
            if elapsed is not None:
                return elapsed

        log.warning(f"Both HTTPS and HTTP failed for {url}")
        return None

    def _http_request(
        self, url: str, method: str = "HEAD", timeout: float | None = None
    ) -> float | None:
        """发送 HTTP 请求测速.

        Args:
            url: 目标 URL.
            method: HTTP 方法（HEAD 或 GET）.
            timeout: 超时秒数; None 则使用 config.speed_test_timeout.

        Returns:
            响应时间（秒），失败返回 None.
        """
        timeout = timeout or self.config.speed_test_timeout
        try:
            start = time.monotonic()
            with httpx.Client(timeout=timeout, verify=False) as client:
                resp = client.request(method, url)
                elapsed = time.monotonic() - start
            # 2xx/3xx 都算成功（可能有 redirect）
            if resp.status_code < 400:
                log.debug(f"{method} {url}: {resp.status_code} in {elapsed:.2f}s")
                return elapsed
            log.debug(f"{method} {url}: status {resp.status_code}, not usable")
            return None
        except httpx.ConnectError as e:
            log.debug(f"{method} {url}: connect error: {e}")
            return None
        except httpx.TimeoutException:
            log.debug(f"{method} {url}: timeout after {timeout}s")
            return None
        except Exception as e:
            # SSL 错误等
            log.debug(f"{method} {url}: error: {type(e).__name__}: {e}")
            return None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def selected_url(self) -> str:
        """当前选定的 API URL."""
        return self._selected_url

    @property
    def selected_response_time(self) -> float:
        """镜像站测速响应时间（秒）."""
        return getattr(self, "_selected_time", 0.0)

    # ------------------------------------------------------------------
    # 数据获取（pro_api.query）
    # ------------------------------------------------------------------

    def fetch(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        offset: int = 0,
        limit: int = 5000,
    ) -> FetchResult:
        """调用 tushare pro_api 接口获取数据.

        See Also:
            :meth:`BaseFetcher.fetch`
        """
        call_params: dict[str, Any] = dict(params or {})
        if start_date is not None:
            call_params["start_date"] = self._fmt_date(start_date)
        if end_date is not None:
            call_params["end_date"] = self._fmt_date(end_date)

        # tushare 分页
        call_params["offset"] = offset
        call_params["limit"] = limit

        log.debug(f"tushare {api_name} call: {call_params}")
        # 支持调用方通过 params 传入 fields 参数（如显式请求 content 等非默认字段）
        fields_val = call_params.pop("fields", "")
        df: pd.DataFrame = self._api.query(
            api_name=api_name,
            fields=fields_val,
            **call_params,
        )
        row_count = len(df) if df is not None else 0
        has_more = row_count >= limit
        return FetchResult(data=df if df is not None else pd.DataFrame(), has_more=has_more)

    # ------------------------------------------------------------------
    # pro_bar 等模块级函数
    # ------------------------------------------------------------------

    def pro_bar(
        self,
        ts_code: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        adj: str | None = None,
        freq: str = "D",
        asset: str = "E",
        market: str = "",
    ) -> pd.DataFrame:
        """调用 ts.pro_bar() 获取 K 线数据（含复权）.

        **重要**: ts.pro_bar() 是模块级函数，必须传 api=self._api
        才能使用镜像站地址，否则会走默认 tushare 官方地址。

        Args:
            ts_code: 股票代码，如 "002594.SZ".
            start_date: 开始日期.
            end_date: 结束日期.
            adj: 复权类型: None=不复权, "qfq"=前复权, "hfq"=后复权.
            freq: 频率: "D"=日线, "W"=周线, "M"=月线.
            asset: 资产类型: "E"=股票, "I"=指数, "FD"=基金.
            market: 市场标识.

        Returns:
            DataFrame.
        """
        self._throttle()

        call_kwargs: dict[str, Any] = {
            "ts_code": ts_code,
            "api": self._api,  # 关键：必须传 api 参数才能使用镜像站
        }
        if start_date is not None:
            call_kwargs["start_date"] = self._fmt_date(start_date)
        if end_date is not None:
            call_kwargs["end_date"] = self._fmt_date(end_date)
        if adj is not None:
            call_kwargs["adj"] = adj
        if freq != "D":
            call_kwargs["freq"] = freq
        if asset != "E":
            call_kwargs["asset"] = asset
        if market:
            call_kwargs["market"] = market

        log.debug(f"ts.pro_bar call: {call_kwargs}")
        df = ts.pro_bar(**call_kwargs)
        return df if df is not None else pd.DataFrame()

    # ------------------------------------------------------------------
    # 股票列表
    # ------------------------------------------------------------------

    def get_stock_list(self) -> list[str]:
        """获取全部 A 股代码列表."""
        result = self.fetch_all(
            api_name="stock_basic",
            params={"exchange": "", "list_status": "L"},
            page_size=self.page_size,
        )
        # tushare 的 Series 类型是 Any，需要显式转换
        codes: list[str] = result["ts_code"].astype(str).tolist()
        return codes

    # ------------------------------------------------------------------
    # 日期格式化
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_date(d: str | date) -> str:
        """统一日期格式为 YYYYMMDD（tushare 格式）."""
        if isinstance(d, str):
            # 兼容 YYYY-MM-DD
            return d.replace("-", "")
        return d.strftime("%Y%m%d")

    # ------------------------------------------------------------------
    # 并发拉取多天数据（DAILY 模式提速）
    # ------------------------------------------------------------------

    def fetch_concurrent(
        self,
        api_name: str,
        trade_dates: list[date],
        params: dict[str, Any] | None = None,
        page_size: int = 5000,
        max_workers: int = 3,
    ) -> dict[date, pd.DataFrame]:
        """并发拉取多个 trade_date 的数据.

        使用线程池并发拉取，每条线程独立限速（共享 fetcher 的 throttle）。
        由于 tushare API 限速（400/min），max_workers 不宜过大（建议 2~5）。
        实际并发数受 API 限速约束，不会真正超过 rate_limit_per_min。

        Args:
            api_name: 接口名称.
            trade_dates: 要拉取的交易日列表.
            params: 基础参数（不含 trade_date）.
            page_size: 单次拉取行数上限.
            max_workers: 并发线程数（默认 3）.

        Returns:
            {trade_date: DataFrame} 字典，空 DataFrame 表示该日无数据.
        """
        results: dict[date, pd.DataFrame] = {}

        def _fetch_one_day(td: date) -> tuple[date, pd.DataFrame]:
            """拉取单日数据（线程内执行）."""
            try:
                df = self.fetch_all(
                    api_name=api_name,
                    params={**dict(params or {}), "trade_date": self._fmt_date(td)},
                    page_size=page_size,
                )
                return td, df
            except Exception as e:
                log.warning(f"fetch_concurrent {api_name} {td}: {e}")
                return td, pd.DataFrame()

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_fetch_one_day, td): td
                for td in trade_dates
            }
            for future in as_completed(futures):
                td, df = future.result()
                results[td] = df

        return results

    # ------------------------------------------------------------------
    # 限速容错
    # ------------------------------------------------------------------

    COOLDOWN_SECONDS = 360  # 6 分钟冷却

    @staticmethod
    def is_rate_limit_error(error: str | Exception) -> bool:
        """判断是否为 tushare 超速/冷却错误."""
        msg = str(error)
        return any(kw in msg for kw in ("访问频率已超速", "冷却", "超速"))

    def wait_for_cooldown(self, retry_interval: int = 60) -> bool:
        """等待 tushare 冷却结束.

        每分钟检查一次是否恢复（通过尝试轻量 API 调用）。
        返回 True 表示恢复，False 表示超时或无法恢复.
        """
        import time as time_mod

        log.warning(
            f"Rate limit hit — waiting {self.COOLDOWN_SECONDS}s cooldown, "
            f"retrying every {retry_interval}s..."
        )
        deadline = time_mod.monotonic() + self.COOLDOWN_SECONDS + 60
        while time_mod.monotonic() < deadline:
            time_mod.sleep(retry_interval)
            try:
                self._throttle()
                import tushare as ts

                result = self._api.query("stock_basic", exchange="", list_status="L", limit=1)
                if result is not None and not (hasattr(result, "empty") and result.empty):
                    log.info("Cooldown ended — resuming")
                    return True
            except Exception as e:
                if not self.is_rate_limit_error(e):
                    log.warning(f"Probe failed with non-rate-limit error: {e}")
                else:
                    log.debug(f"Still in cooldown: {e}")

        log.warning("Cooldown wait timeout — resuming anyway")
        return False
