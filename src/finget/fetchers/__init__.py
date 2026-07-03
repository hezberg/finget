"""数据获取层."""

from finget.fetchers.base import BaseFetcher, FetchResult
from finget.fetchers.tushare_fetcher import TushareFetcher

__all__ = ["BaseFetcher", "FetchResult", "TushareFetcher"]
