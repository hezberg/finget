"""线程安全的只读 DuckDB 访问封装，供 FastAPI server 使用."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd

from finget.storage.duckdb_store import DuckDBStore


class StoreReader:
    """线程安全的 DuckDB 只读访问.

    单连接 + threading.Lock 确保并发安全。
    所有方法返回 pandas DataFrame 或可序列化结果。
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._store: DuckDBStore | None = None
        self._lock = threading.Lock()

    @property
    def store(self) -> DuckDBStore:
        if self._store is None:
            from finget.config import StorageConfig

            self._store = DuckDBStore(StorageConfig(db_path=self.db_path))
        return self._store

    def query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        with self._lock:
            return self.store.query(sql, params)

    def list_tables(self) -> list[str]:
        with self._lock:
            return self.store.list_tables()

    def close(self) -> None:
        with self._lock:
            if self._store:
                self._store.close()
                self._store = None


def get_store_reader() -> StoreReader:
    """获取全局 StoreReader 单例.

    serve 命令只需要 DB 文件路径，不需要 tushare token。
    因此直接从环境变量读取 db_path，绕过 get_config() 的 token 强制校验。
    """
    global _store_reader
    if _store_reader is None:
        import os
        from pathlib import Path

        db_path = os.environ.get("FINGET_DB_PATH", "data/finget.duckdb")
        # 相对路径基于项目根目录解析
        if not os.path.isabs(db_path):
            _project_root = Path(__file__).resolve().parent.parent.parent.parent
            db_path = str(_project_root / db_path)
        _store_reader = StoreReader(db_path)
    return _store_reader


_store_reader: StoreReader | None = None
