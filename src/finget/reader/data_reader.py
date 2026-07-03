"""独立数据读取接口，供下游调用."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import pandas as pd

from finget.logging import log
from finget.storage.duckdb_store import DuckDBStore


class DataReader:
    """面向下游消费方的数据读取接口.

    提供高层语义化查询方法，隐藏 SQL 细节。
    所有方法返回 pandas DataFrame。
    """

    def __init__(self, store: DuckDBStore) -> None:
        self.store = store

    # ------------------------------------------------------------------
    # K 线数据
    # ------------------------------------------------------------------

    def get_kline(
        self,
        ts_code: str | list[str] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        table: str = "daily",
    ) -> pd.DataFrame:
        """获取 K 线数据.

        Args:
            ts_code: 单个或多个标的代码; None 则全部.
            start_date: 开始日期.
            end_date: 结束日期.
            table: 数据表名 (daily / weekly).

        Returns:
            DataFrame, 按日期升序排列.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if ts_code is not None:
            if isinstance(ts_code, str):
                conditions.append("ts_code = ?")
                params.append(ts_code)
            else:
                placeholders = ", ".join(["?"] * len(ts_code))
                conditions.append(f"ts_code IN ({placeholders})")
                params.extend(ts_code)

        if start_date is not None:
            conditions.append("trade_date >= ?")
            params.append(self._to_date(start_date))

        if end_date is not None:
            conditions.append("trade_date <= ?")
            params.append(self._to_date(end_date))

        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM {table}{where} ORDER BY ts_code, trade_date;"
        return self.store.query(sql, params)

    def get_close(
        self,
        ts_code: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        table: str = "daily",
    ) -> pd.Series:
        """获取收盘价序列（索引为日期）."""
        df = self.get_kline(ts_code, start_date, end_date, table)
        if df.empty:
            return pd.Series(dtype=float, name=ts_code)
        s = df.set_index("trade_date")["close"]
        s.name = ts_code
        return s

    def get_adj_factor(
        self,
        ts_code: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """获取复权因子."""
        conditions = ["ts_code = ?"]
        params: list[Any] = [ts_code]
        if start_date is not None:
            conditions.append("trade_date >= ?")
            params.append(self._to_date(start_date))
        if end_date is not None:
            conditions.append("trade_date <= ?")
            params.append(self._to_date(end_date))
        sql = (
            f"SELECT * FROM adj_factor WHERE {' AND '.join(conditions)} "
            f"ORDER BY trade_date;"
        )
        return self.store.query(sql, params)

    # ------------------------------------------------------------------
    # 基础信息
    # ------------------------------------------------------------------

    def get_stock_basic(
        self,
        ts_code: str | None = None,
        industry: str | None = None,
        list_status: str = "L",
    ) -> pd.DataFrame:
        """获取股票基础信息."""
        conditions: list[str] = []
        params: list[Any] = []
        if ts_code:
            conditions.append("ts_code = ?")
            params.append(ts_code)
        if industry:
            conditions.append("industry = ?")
            params.append(industry)
        if list_status:
            conditions.append("list_status = ?")
            params.append(list_status)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM stock_basic{where} ORDER BY ts_code;"
        return self.store.query(sql, params)

    def get_hk_us_basic(self, ts_code: str | None = None) -> pd.DataFrame:
        """获取港美股基础信息（港股+美股合并表）.

        Args:
            ts_code: 股票代码（如 00700.HK / AAPL.US）; None 则全部.

        Returns:
            DataFrame, 按 ts_code 排序.
        """
        if ts_code:
            sql = "SELECT * FROM hk_us_basic WHERE ts_code = ? ORDER BY ts_code;"
            return self.store.query(sql, [ts_code])
        sql = "SELECT * FROM hk_us_basic ORDER BY ts_code;"
        return self.store.query(sql)

    def get_stock_list(self, industry: str | None = None) -> list[str]:
        """获取股票代码列表."""
        df = self.get_stock_basic(industry=industry)
        return df["ts_code"].tolist() if not df.empty else []

    # ------------------------------------------------------------------
    # 指标
    # ------------------------------------------------------------------

    def get_daily_basic(
        self,
        ts_code: str | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """获取每日指标（PE/PB/换手率等）."""
        conditions: list[str] = []
        params: list[Any] = []
        if ts_code:
            conditions.append("ts_code = ?")
            params.append(ts_code)
        if start_date is not None:
            conditions.append("trade_date >= ?")
            params.append(self._to_date(start_date))
        if end_date is not None:
            conditions.append("trade_date <= ?")
            params.append(self._to_date(end_date))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM daily_basic{where} ORDER BY ts_code, trade_date;"
        return self.store.query(sql, params)

    # ------------------------------------------------------------------
    # 技术面因子
    # ------------------------------------------------------------------

    def get_stk_factor(
        self,
        ts_code: str | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> pd.DataFrame:
        """获取股票技术面因子数据（MACD/KDJ/RSI/BOLL/EMA/MA 等）.

        Args:
            ts_code: 股票代码; None 则全部.
            start_date: 开始日期.
            end_date: 结束日期.

        Returns:
            DataFrame, 按日期升序排列.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if ts_code:
            conditions.append("ts_code = ?")
            params.append(ts_code)
        if start_date is not None:
            conditions.append("trade_date >= ?")
            params.append(self._to_date(start_date))
        if end_date is not None:
            conditions.append("trade_date <= ?")
            params.append(self._to_date(end_date))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM stk_factor_pro{where} ORDER BY ts_code, trade_date;"
        return self.store.query(sql, params)

    # ------------------------------------------------------------------
    # 券商月度金股
    # ------------------------------------------------------------------

    def get_broker_recommend(
        self,
        ts_code: str | None = None,
        broker: str | None = None,
        month: str | None = None,
    ) -> pd.DataFrame:
        """获取券商月度金股数据.

        Args:
            ts_code: 股票代码; None 则全部.
            broker: 券商名称; None 则全部.
            month: 月度 (YYYYMM); None 则全部.

        Returns:
            DataFrame, 按月份降序排列.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if ts_code:
            conditions.append("ts_code = ?")
            params.append(ts_code)
        if broker:
            conditions.append("broker = ?")
            params.append(broker)
        if month:
            conditions.append("month = ?")
            params.append(month)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM broker_recommend{where} ORDER BY month DESC, broker, ts_code;"
        return self.store.query(sql, params)

    # ------------------------------------------------------------------
    # 机构调研
    # ------------------------------------------------------------------

    def get_survey(
        self,
        ts_code: str | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        with_content: bool = False,
    ) -> pd.DataFrame:
        """获取机构调研记录.

        Args:
            ts_code: 股票代码; None 则全部.
            start_date: 调研开始日期.
            end_date: 调研结束日期.
            with_content: True 时 LEFT JOIN stk_surv_detail 带出 content 大文本;
                False 时只查主表（快，适合统计场景）.

        Returns:
            DataFrame, 按 surv_date 降序排列.
        """
        conditions: list[str] = []
        params: list[Any] = []
        if ts_code:
            conditions.append("s.ts_code = ?")
            params.append(ts_code)
        if start_date is not None:
            conditions.append("s.surv_date >= ?")
            params.append(self._to_date(start_date))
        if end_date is not None:
            conditions.append("s.surv_date <= ?")
            params.append(self._to_date(end_date))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""

        if with_content:
            sql = (
                f"SELECT s.*, d.content FROM stk_surv s"
                f" LEFT JOIN stk_surv_detail d"
                f" ON s.ts_code = d.ts_code AND s.surv_date = d.surv_date AND s.rece_org = d.rece_org"
                f"{where} ORDER BY s.surv_date DESC, s.ts_code;"
            )
        else:
            sql = f"SELECT * FROM stk_surv s{where} ORDER BY surv_date DESC, ts_code;"
        return self.store.query(sql, params)

    # ------------------------------------------------------------------
    # 通用查询
    # ------------------------------------------------------------------

    def raw_query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        """执行任意 SQL 查询."""
        return self.store.query(sql, params)

    def list_tables(self) -> list[str]:
        """列出所有数据表."""
        return self.store.list_tables()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _to_date(d: str | date | datetime) -> date:
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, str):
            return datetime.strptime(d.replace("-", ""), "%Y%m%d").date()
        return d
