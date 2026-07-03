"""数据获取层抽象基类."""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd

from finget.logging import log


@dataclass
class FetchResult:
    """单次获取结果."""

    data: pd.DataFrame
    # 是否还有更多数据（分页）
    has_more: bool = False
    # 本次获取行数
    row_count: int = field(init=False)

    def __post_init__(self) -> None:
        self.row_count = len(self.data) if self.data is not None else 0


class BaseFetcher(ABC):
    """数据源适配器抽象基类.

    所有数据源（tushare、东方财富、自定义 HTTP 等）均需实现此接口。
    """

    def __init__(self, rate_limit_per_min: int = 200) -> None:
        self.rate_limit_per_min = rate_limit_per_min
        # 最小间隔时间（秒），用于限速
        self._min_interval = 60.0 / rate_limit_per_min if rate_limit_per_min > 0 else 0
        self._last_call_ts: float = 0.0
        self._throttle_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 限速
    # ------------------------------------------------------------------

    def _throttle(self) -> None:
        """确保调用间隔不小于 _min_interval，防止触发频率限制.

        使用线程锁保护，保证多线程并发拉取时限速计时准确。
        """
        if self._min_interval <= 0:
            return
        with self._throttle_lock:
            elapsed = time.monotonic() - self._last_call_ts
            if elapsed < self._min_interval:
                time.sleep(self._min_interval - elapsed)
            self._last_call_ts = time.monotonic()

    # ------------------------------------------------------------------
    # 抽象接口
    # ------------------------------------------------------------------

    @abstractmethod
    def fetch(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        offset: int = 0,
        limit: int = 5000,
    ) -> FetchResult:
        """获取数据.

        Args:
            api_name: 接口名称（如 "daily"）.
            params: 附加参数.
            start_date: 开始日期.
            end_date: 结束日期.
            offset: 分页偏移.
            limit: 单次最大行数.

        Returns:
            FetchResult.
        """
        ...

    # ------------------------------------------------------------------
    # 分页拉取全部
    # ------------------------------------------------------------------

    def fetch_all(
        self,
        api_name: str,
        params: dict[str, Any] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        page_size: int = 5000,
        max_pages: int = 10000,
    ) -> pd.DataFrame:
        """分页拉取全部数据.

        Args:
            api_name: 接口名称.
            params: 附加参数.
            start_date: 开始日期.
            end_date: 结束日期.
            page_size: 每页大小.
            max_pages: 最大页数（安全阀）.

        Returns:
            合并后的 DataFrame.
        """
        all_chunks: list[pd.DataFrame] = []
        offset = 0
        for page in range(max_pages):
            self._throttle()
            result = self.fetch(
                api_name=api_name,
                params=params,
                start_date=start_date,
                end_date=end_date,
                offset=offset,
                limit=page_size,
            )
            if result.row_count == 0:
                log.debug(f"{api_name} page {page}: empty, stop.")
                break
            all_chunks.append(result.data)
            offset += result.row_count
            log.debug(f"{api_name} page {page}: fetched {result.row_count} rows")
            if not result.has_more or result.row_count < page_size:
                break
        if not all_chunks:
            return pd.DataFrame()
        return pd.concat(all_chunks, ignore_index=True)
