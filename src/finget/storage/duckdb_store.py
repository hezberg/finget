"""DuckDB 存储引擎 — 针对金融时序数据优化."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator

import duckdb
import pandas as pd

from finget.config import StorageConfig
from finget.logging import log

# ---------------------------------------------------------------------------
# 数据集 schema 定义
#   每个数据集对应一张 DuckDB 表，字段类型显式声明以便列式存储优化。
#   trade_date 统一存为 DATE 类型，ts_code 存为 VARCHAR。
# ---------------------------------------------------------------------------

SCHEMAS: dict[str, str] = {
    "stock_basic": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code        VARCHAR,
            symbol         VARCHAR,
            name           VARCHAR,
            fullname       VARCHAR,
            enname         VARCHAR,
            cnspell        VARCHAR,
            area           VARCHAR,
            industry       VARCHAR,
            market         VARCHAR,
            exchange       VARCHAR,
            curr_type      VARCHAR,
            list_status    VARCHAR,
            list_date      DATE,
            delist_date    DATE,
            is_hs          VARCHAR,
            act_name       VARCHAR,
            act_ent_type   VARCHAR
        )
    """,
    "daily": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code     VARCHAR,
            trade_date  DATE,
            open        DOUBLE,
            high        DOUBLE,
            low         DOUBLE,
            close       DOUBLE,
            pre_close   DOUBLE,
            change      DOUBLE,
            pct_chg     DOUBLE,
            vol         DOUBLE,
            amount      DOUBLE
        )
    """,
    "weekly": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code     VARCHAR,
            trade_date  DATE,
            open        DOUBLE,
            high        DOUBLE,
            low         DOUBLE,
            close       DOUBLE,
            vol         DOUBLE,
            amount      DOUBLE
        )
    """,
    "adj_factor": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code     VARCHAR,
            trade_date  DATE,
            adj_factor  DOUBLE
        )
    """,
    "daily_basic": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code            VARCHAR,
            trade_date         DATE,
            close              DOUBLE,
            turnover_rate      DOUBLE,
            turnover_rate_f    DOUBLE,
            volume_ratio       DOUBLE,
            pe                 DOUBLE,
            pe_ttm             DOUBLE,
            pb                 DOUBLE,
            ps                 DOUBLE,
            ps_ttm             DOUBLE,
            dv_ratio           DOUBLE,
            dv_ttm             DOUBLE,
            total_share        DOUBLE,
            float_share        DOUBLE,
            free_share         DOUBLE,
            total_mv           DOUBLE,
            circ_mv            DOUBLE
        )
    """,
    "trade_cal": """
        CREATE TABLE IF NOT EXISTS {table} (
            exchange        VARCHAR,
            cal_date        DATE,
            is_open         BOOLEAN,
            pretrade_date   DATE
        )
    """,
    "report_rc": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code        VARCHAR,
            name           VARCHAR,
            report_date    DATE,
            report_title   VARCHAR,
            report_type    VARCHAR,
            classify       VARCHAR,
            org_name       VARCHAR,
            author_name    VARCHAR,
            quarter        VARCHAR,
            op_rt          DOUBLE,
            op_pr          DOUBLE,
            tp             DOUBLE,
            np             DOUBLE,
            eps            DOUBLE,
            pe             DOUBLE,
            rd             DOUBLE,
            roe            DOUBLE,
            ev_ebitda      DOUBLE,
            rating         VARCHAR,
            max_price      DOUBLE,
            min_price      DOUBLE,
            imp_dg         VARCHAR,
            create_time    TIMESTAMP
        )
    """,
    "stk_factor_pro": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code                VARCHAR,
            trade_date             DATE,
            open                   DOUBLE,
            open_hfq               DOUBLE,
            open_qfq               DOUBLE,
            high                   DOUBLE,
            high_hfq               DOUBLE,
            high_qfq               DOUBLE,
            low                    DOUBLE,
            low_hfq                DOUBLE,
            low_qfq                DOUBLE,
            close                  DOUBLE,
            close_hfq              DOUBLE,
            close_qfq              DOUBLE,
            pre_close              DOUBLE,
            change                 DOUBLE,
            pct_chg                DOUBLE,
            vol                    DOUBLE,
            amount                 DOUBLE,
            turnover_rate          DOUBLE,
            turnover_rate_f        DOUBLE,
            volume_ratio           DOUBLE,
            pe                     DOUBLE,
            pe_ttm                 DOUBLE,
            pb                     DOUBLE,
            ps                     DOUBLE,
            ps_ttm                 DOUBLE,
            dv_ratio               DOUBLE,
            dv_ttm                 DOUBLE,
            total_share            DOUBLE,
            float_share            DOUBLE,
            free_share             DOUBLE,
            total_mv               DOUBLE,
            circ_mv                DOUBLE,
            adj_factor             DOUBLE,
            asi_bfq                DOUBLE,
            asi_hfq                DOUBLE,
            asi_qfq                DOUBLE,
            asit_bfq               DOUBLE,
            asit_hfq               DOUBLE,
            asit_qfq               DOUBLE,
            atr_bfq                DOUBLE,
            atr_hfq                DOUBLE,
            atr_qfq                DOUBLE,
            bbi_bfq                DOUBLE,
            bbi_hfq                DOUBLE,
            bbi_qfq                DOUBLE,
            bias1_bfq              DOUBLE,
            bias1_hfq              DOUBLE,
            bias1_qfq              DOUBLE,
            bias2_bfq              DOUBLE,
            bias2_hfq              DOUBLE,
            bias2_qfq              DOUBLE,
            bias3_bfq              DOUBLE,
            bias3_hfq              DOUBLE,
            bias3_qfq              DOUBLE,
            boll_lower_bfq         DOUBLE,
            boll_lower_hfq         DOUBLE,
            boll_lower_qfq         DOUBLE,
            boll_mid_bfq           DOUBLE,
            boll_mid_hfq           DOUBLE,
            boll_mid_qfq           DOUBLE,
            boll_upper_bfq         DOUBLE,
            boll_upper_hfq         DOUBLE,
            boll_upper_qfq         DOUBLE,
            brar_ar_bfq            DOUBLE,
            brar_ar_hfq            DOUBLE,
            brar_ar_qfq            DOUBLE,
            brar_br_bfq            DOUBLE,
            brar_br_hfq            DOUBLE,
            brar_br_qfq            DOUBLE,
            cci_bfq                DOUBLE,
            cci_hfq                DOUBLE,
            cci_qfq                DOUBLE,
            cr_bfq                 DOUBLE,
            cr_hfq                 DOUBLE,
            cr_qfq                 DOUBLE,
            dfma_dif_bfq           DOUBLE,
            dfma_dif_hfq           DOUBLE,
            dfma_dif_qfq           DOUBLE,
            dfma_difma_bfq         DOUBLE,
            dfma_difma_hfq         DOUBLE,
            dfma_difma_qfq         DOUBLE,
            dmi_adx_bfq            DOUBLE,
            dmi_adx_hfq            DOUBLE,
            dmi_adx_qfq            DOUBLE,
            dmi_adxr_bfq           DOUBLE,
            dmi_adxr_hfq           DOUBLE,
            dmi_adxr_qfq           DOUBLE,
            dmi_mdi_bfq            DOUBLE,
            dmi_mdi_hfq            DOUBLE,
            dmi_mdi_qfq            DOUBLE,
            dmi_pdi_bfq            DOUBLE,
            dmi_pdi_hfq            DOUBLE,
            dmi_pdi_qfq            DOUBLE,
            downdays               DOUBLE,
            updays                 DOUBLE,
            dpo_bfq                DOUBLE,
            dpo_hfq                DOUBLE,
            dpo_qfq                DOUBLE,
            madpo_bfq              DOUBLE,
            madpo_hfq              DOUBLE,
            madpo_qfq              DOUBLE,
            ema_bfq_10             DOUBLE,
            ema_bfq_20             DOUBLE,
            ema_bfq_250            DOUBLE,
            ema_bfq_30             DOUBLE,
            ema_bfq_5              DOUBLE,
            ema_bfq_60             DOUBLE,
            ema_bfq_90             DOUBLE,
            ema_hfq_10             DOUBLE,
            ema_hfq_20             DOUBLE,
            ema_hfq_250            DOUBLE,
            ema_hfq_30             DOUBLE,
            ema_hfq_5              DOUBLE,
            ema_hfq_60             DOUBLE,
            ema_hfq_90             DOUBLE,
            ema_qfq_10             DOUBLE,
            ema_qfq_20             DOUBLE,
            ema_qfq_250            DOUBLE,
            ema_qfq_30             DOUBLE,
            ema_qfq_5              DOUBLE,
            ema_qfq_60             DOUBLE,
            ema_qfq_90             DOUBLE,
            emv_bfq                DOUBLE,
            emv_hfq                DOUBLE,
            emv_qfq                DOUBLE,
            maemv_bfq              DOUBLE,
            maemv_hfq              DOUBLE,
            maemv_qfq              DOUBLE,
            expma_12_bfq           DOUBLE,
            expma_12_hfq           DOUBLE,
            expma_12_qfq           DOUBLE,
            expma_50_bfq           DOUBLE,
            expma_50_hfq           DOUBLE,
            expma_50_qfq           DOUBLE,
            kdj_bfq                DOUBLE,
            kdj_hfq                DOUBLE,
            kdj_qfq                DOUBLE,
            kdj_d_bfq              DOUBLE,
            kdj_d_hfq              DOUBLE,
            kdj_d_qfq              DOUBLE,
            kdj_k_bfq              DOUBLE,
            kdj_k_hfq              DOUBLE,
            kdj_k_qfq              DOUBLE,
            ktn_down_bfq           DOUBLE,
            ktn_down_hfq           DOUBLE,
            ktn_down_qfq           DOUBLE,
            ktn_mid_bfq            DOUBLE,
            ktn_mid_hfq            DOUBLE,
            ktn_mid_qfq            DOUBLE,
            ktn_upper_bfq          DOUBLE,
            ktn_upper_hfq          DOUBLE,
            ktn_upper_qfq          DOUBLE,
            lowdays                DOUBLE,
            topdays                DOUBLE,
            ma_bfq_10              DOUBLE,
            ma_bfq_20              DOUBLE,
            ma_bfq_250             DOUBLE,
            ma_bfq_30              DOUBLE,
            ma_bfq_5               DOUBLE,
            ma_bfq_60              DOUBLE,
            ma_bfq_90              DOUBLE,
            ma_hfq_10              DOUBLE,
            ma_hfq_20              DOUBLE,
            ma_hfq_250             DOUBLE,
            ma_hfq_30              DOUBLE,
            ma_hfq_5               DOUBLE,
            ma_hfq_60              DOUBLE,
            ma_hfq_90              DOUBLE,
            ma_qfq_10              DOUBLE,
            ma_qfq_20              DOUBLE,
            ma_qfq_250             DOUBLE,
            ma_qfq_30              DOUBLE,
            ma_qfq_5               DOUBLE,
            ma_qfq_60              DOUBLE,
            ma_qfq_90              DOUBLE,
            macd_bfq               DOUBLE,
            macd_hfq               DOUBLE,
            macd_qfq               DOUBLE,
            macd_dea_bfq           DOUBLE,
            macd_dea_hfq           DOUBLE,
            macd_dea_qfq           DOUBLE,
            macd_dif_bfq           DOUBLE,
            macd_dif_hfq           DOUBLE,
            macd_dif_qfq           DOUBLE,
            mass_bfq               DOUBLE,
            mass_hfq               DOUBLE,
            mass_qfq               DOUBLE,
            ma_mass_bfq            DOUBLE,
            ma_mass_hfq            DOUBLE,
            ma_mass_qfq            DOUBLE,
            mfi_bfq                DOUBLE,
            mfi_hfq                DOUBLE,
            mfi_qfq                DOUBLE,
            mtm_bfq                DOUBLE,
            mtm_hfq                DOUBLE,
            mtm_qfq                DOUBLE,
            mtmma_bfq              DOUBLE,
            mtmma_hfq              DOUBLE,
            mtmma_qfq              DOUBLE,
            obv_bfq                DOUBLE,
            obv_hfq                DOUBLE,
            obv_qfq                DOUBLE,
            psy_bfq                DOUBLE,
            psy_hfq                DOUBLE,
            psy_qfq                DOUBLE,
            psyma_bfq              DOUBLE,
            psyma_hfq              DOUBLE,
            psyma_qfq              DOUBLE,
            roc_bfq                DOUBLE,
            roc_hfq                DOUBLE,
            roc_qfq                DOUBLE,
            maroc_bfq              DOUBLE,
            maroc_hfq              DOUBLE,
            maroc_qfq              DOUBLE,
            rsi_bfq_12             DOUBLE,
            rsi_bfq_24             DOUBLE,
            rsi_bfq_6              DOUBLE,
            rsi_hfq_12             DOUBLE,
            rsi_hfq_24             DOUBLE,
            rsi_hfq_6              DOUBLE,
            rsi_qfq_12             DOUBLE,
            rsi_qfq_24             DOUBLE,
            rsi_qfq_6              DOUBLE,
            taq_down_bfq           DOUBLE,
            taq_down_hfq           DOUBLE,
            taq_down_qfq           DOUBLE,
            taq_mid_bfq            DOUBLE,
            taq_mid_hfq            DOUBLE,
            taq_mid_qfq            DOUBLE,
            taq_up_bfq             DOUBLE,
            taq_up_hfq             DOUBLE,
            taq_up_qfq             DOUBLE,
            trix_bfq               DOUBLE,
            trix_hfq               DOUBLE,
            trix_qfq               DOUBLE,
            trma_bfq               DOUBLE,
            trma_hfq               DOUBLE,
            trma_qfq               DOUBLE,
            vr_bfq                 DOUBLE,
            vr_hfq                 DOUBLE,
            vr_qfq                 DOUBLE,
            wr_bfq                 DOUBLE,
            wr_hfq                 DOUBLE,
            wr_qfq                 DOUBLE,
            wr1_bfq                DOUBLE,
            wr1_hfq                DOUBLE,
            wr1_qfq                DOUBLE,
            xsii_td1_bfq           DOUBLE,
            xsii_td1_hfq           DOUBLE,
            xsii_td1_qfq           DOUBLE,
            xsii_td2_bfq           DOUBLE,
            xsii_td2_hfq           DOUBLE,
            xsii_td2_qfq           DOUBLE,
            xsii_td3_bfq           DOUBLE,
            xsii_td3_hfq           DOUBLE,
            xsii_td3_qfq           DOUBLE,
            xsii_td4_bfq           DOUBLE,
            xsii_td4_hfq           DOUBLE,
            xsii_td4_qfq           DOUBLE
        )
    """,
    "broker_recommend": """
        CREATE TABLE IF NOT EXISTS {table} (
            month       VARCHAR,
            broker      VARCHAR,
            ts_code     VARCHAR,
            name        VARCHAR
        )
    """,
    # 机构调研主表（窄表，不含 content 大文本，便于统计查询）
    "stk_surv": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code        VARCHAR,
            name           VARCHAR,
            surv_date      DATE,
            fund_visitors  VARCHAR,
            rece_place     VARCHAR,
            rece_mode      VARCHAR,
            rece_org       VARCHAR,
            org_type       VARCHAR,
            comp_rece      VARCHAR
        )
    """,
    # 机构调研详情表（隔离 content 大文本，按需 JOIN，避免拖慢主表扫描）
    "stk_surv_detail": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code   VARCHAR,
            surv_date DATE,
            rece_org  VARCHAR,
            content   VARCHAR
        )
    """,
    # 港美股基础信息（港股 hk_basic + 美股 us_basic 合并，按 ts_code 后缀区分市场）
    "hk_us_basic": """
        CREATE TABLE IF NOT EXISTS {table} (
            ts_code   VARCHAR,
            name      VARCHAR,
            enname    VARCHAR
        )
    """,
}

# 数据集类型 → UNIQUE 索引列
# 显式声明集中管理，避免 init_table() 写一大堆 if/elif
_CONFLICT_KEYS: dict[str, list[str]] = {
    "stock_basic": ["ts_code"],
    "trade_cal": ["exchange", "cal_date"],
    # report_rc: 同一股票同一日可能有多个机构/作者发研报，按 (股票, 日期, 机构, 作者, 季度) 去重
    "report_rc": ["ts_code", "report_date", "org_name", "author_name", "quarter"],
    # broker_recommend: 每月每券商每股票一条金股，按 (月度, 券商, 股票) 去重
    "broker_recommend": ["month", "broker", "ts_code"],
    # stk_surv: 同一股票同一日同一接待机构为一条调研记录
    "stk_surv": ["ts_code", "surv_date", "rece_org"],
    "stk_surv_detail": ["ts_code", "surv_date", "rece_org"],
    # hk_us_basic: 港股和美股 ts_code 后缀不同（.HK/.US），不会冲突
    "hk_us_basic": ["ts_code"],
}

# 时序数据集（含 trade_date），用于增量/补漏判断
TIME_SERIES_DATASETS = {"daily", "weekly", "adj_factor", "daily_basic", "stk_factor_pro"}

# 日历类数据集（按 (exchange, cal_date) 去重，整体一次拉取，不分 ts_code）
CALENDAR_DATASETS = {"trade_cal"}

# 券商月度金股类数据集（按 month 拉取，按 (month, broker, ts_code) 去重）
BROKER_DATASETS = {"broker_recommend"}

# 机构调研类数据集（按 ts_code 逐标的拉取，content 大文本拆 detail 表）
SURVEY_DATASETS = {"stk_surv"}

# 港美股基础信息类数据集（hk_basic + us_basic 两个接口合并写入一张表）
HK_US_BASIC_DATASETS = {"hk_us_basic"}


class DuckDBStore:
    """DuckDB 存储引擎.

    特性:
        - 列式存储，针对 OLAP 分析优化.
        - 单文件数据库，零运维，可直接拷贝迁移.
        - 基于 (ts_code, trade_date) 去重，支持幂等写入.
        - 自动创建分区/索引以加速时序查询.
    """

    def __init__(self, config: StorageConfig) -> None:
        self.config = config
        if config.in_memory:
            self._db_path = ":memory:"
        else:
            self._db_path = str(Path(config.db_path).resolve())
            Path(config.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: duckdb.DuckDBPyConnection | None = None
        log.debug(f"DuckDBStore: db_path={self._db_path}")

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(self._db_path, read_only=False)
            # 性能调优
            self._conn.execute("SET threads TO 4;")
            self._conn.execute("SET memory_limit='2GB';")
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Iterator[duckdb.DuckDBPyConnection]:
        """事务上下文管理器."""
        conn = self.conn
        conn.execute("BEGIN TRANSACTION;")
        try:
            yield conn
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    # ------------------------------------------------------------------
    # DDL
    # ------------------------------------------------------------------

    def init_table(self, dataset_type: str, table_name: str | None = None) -> None:
        """初始化数据表.

        Args:
            dataset_type: 数据集类型（如 "daily"）.
            table_name: 表名; None 则与 dataset_type 相同.
        """
        table = table_name or dataset_type
        if dataset_type not in SCHEMAS:
            raise ValueError(f"Unknown dataset type: {dataset_type}")
        ddl = SCHEMAS[dataset_type].format(table=table)
        self.conn.execute(ddl)

        # 创建 UNIQUE 索引以支持 ON CONFLICT 幂等写入
        if dataset_type in _CONFLICT_KEYS:
            self._create_unique_index(table, _CONFLICT_KEYS[dataset_type])
        elif dataset_type in TIME_SERIES_DATASETS:
            self._create_unique_index(table, ["ts_code", "trade_date"])

        log.info(f"Table '{table}' initialized (type={dataset_type})")

    def init_all(self, drop_existing: bool = False) -> None:
        """初始化所有已知数据表.

        Args:
            drop_existing: 若为 True，会先删除已存在的表再重建（用于 schema 升级）。
                           默认 False，保留已有数据。
        """
        for ds_type in SCHEMAS:
            if drop_existing and self.table_exists(ds_type):
                log.info(f"Dropping existing table '{ds_type}' for schema upgrade")
                self.drop_table(ds_type)
            self.init_table(ds_type)

    @staticmethod
    def _index_name(table: str, cols: list[str]) -> str:
        return f"uidx_{table}_{'_'.join(cols)}"

    def _create_unique_index(self, table: str, cols: list[str]) -> None:
        """安全创建 UNIQUE 索引（已存在则忽略）."""
        idx_name = self._index_name(table, cols)
        col_list = ", ".join(cols)
        try:
            self.conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} ON {table}({col_list});"
            )
        except Exception as e:
            log.debug(f"Index {idx_name} creation skipped: {e}")

    def table_exists(self, table_name: str) -> bool:
        result = self.conn.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        ).fetchone()
        return result[0] > 0 if result else False

    def drop_table(self, table_name: str) -> None:
        self.conn.execute(f"DROP TABLE IF EXISTS {table_name};")
        log.info(f"Table '{table_name}' dropped.")

    # ------------------------------------------------------------------
    # 写入（幂等去重）
    # ------------------------------------------------------------------

    def upsert(
        self,
        table_name: str,
        df: pd.DataFrame,
        conflict_keys: list[str] | None = None,
    ) -> int:
        """幂等写入（基于 conflict_keys 去重）.

        使用 INSERT ... ON CONFLICT DO REPLACE 语义。

        兼容旧 / 新版 tushare 接口字段差异：
        如果 DataFrame 含表 schema 中不存在的列，自动剔除并 log warning。
        如果表 schema 中存在但 DataFrame 缺失的列，自动用 None 填充。

        Args:
            table_name: 目标表.
            df: 待写入数据.
            conflict_keys: 冲突检测列; None 则自动推断.

        Returns:
            实际写入行数.
        """
        if df.empty:
            return 0

        if not self.table_exists(table_name):
            raise ValueError(f"Table '{table_name}' does not exist. Call init_table first.")

        if conflict_keys is None:
            conflict_keys = self._infer_conflict_keys(table_name)

        # 1. 对齐 DataFrame 与表 schema 的列
        table_cols = self._get_table_columns(table_name)
        df_cols = df.columns.tolist()

        # 1a. DataFrame 多了的列 → 剔除 + warning
        extra_cols = [c for c in df_cols if c not in table_cols]
        if extra_cols:
            log.warning(
                f"Table '{table_name}': dropping {len(extra_cols)} unknown column(s): {extra_cols}. "
                f"Consider updating SCHEMAS to include them."
            )
            df = df.drop(columns=extra_cols)
            df_cols = df.columns.tolist()

        # 1b. 表 schema 多了的列（DataFrame 缺）→ 用 None 填充
        missing_cols = [c for c in table_cols if c not in df_cols]
        if missing_cols:
            for c in missing_cols:
                df[c] = None

        # 2. 检查 conflict_keys 是否在表中
        for k in conflict_keys:
            if k not in table_cols:
                raise ValueError(
                    f"Conflict key '{k}' not in table '{table_name}' columns: {table_cols}"
                )

        # 3. 注册临时视图（此时 df 与表 schema 完全对齐）
        self.conn.register("_tmp_upsert", df)

        cols = ", ".join(table_cols)
        update_set = ", ".join(
            f"{c}=EXCLUDED.{c}" for c in table_cols if c not in conflict_keys
        )
        conflict_cols = ", ".join(conflict_keys)

        if conflict_keys and update_set:
            sql = (
                f"INSERT INTO {table_name} ({cols}) "
                f"SELECT {cols} FROM _tmp_upsert "
                f"ON CONFLICT({conflict_cols}) DO UPDATE SET {update_set};"
            )
        elif conflict_keys:
            sql = (
                f"INSERT INTO {table_name} ({cols}) "
                f"SELECT {cols} FROM _tmp_upsert "
                f"ON CONFLICT({conflict_cols}) DO NOTHING;"
            )
        else:
            sql = f"INSERT INTO {table_name} ({cols}) SELECT {cols} FROM _tmp_upsert;"

        with self.transaction() as conn:
            conn.execute(sql)
        self.conn.unregister("_tmp_upsert")
        log.debug(f"Upserted {len(df)} rows into '{table_name}'")
        return len(df)

    def _infer_conflict_keys(self, table_name: str) -> list[str]:
        """推断去重键.

        优先查 _CONFLICT_KEYS 字典（已注册的数据集），
        否则回退到时序表默认键 (ts_code, trade_date)。
        """
        if table_name in _CONFLICT_KEYS:
            return _CONFLICT_KEYS[table_name]
        # 时序表用 (ts_code, trade_date)
        return ["ts_code", "trade_date"]

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def query(self, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
        """执行 SQL 查询，返回 DataFrame."""
        return self.conn.execute(sql, params or []).fetchdf()

    def get_max_date(self, table_name: str, ts_code: str | None = None) -> date | None:
        """获取表中最大交易日（用于增量更新起点）."""
        if not self.table_exists(table_name):
            return None
        sql = f"SELECT MAX(trade_date) FROM {table_name}"
        if ts_code:
            sql += f" WHERE ts_code = ?"
            result = self.conn.execute(sql, [ts_code]).fetchone()
        else:
            result = self.conn.execute(sql).fetchone()
        return result[0] if result and result[0] else None

    def get_min_date(self, table_name: str, ts_code: str | None = None) -> date | None:
        """获取表中最小交易日."""
        if not self.table_exists(table_name):
            return None
        sql = f"SELECT MIN(trade_date) FROM {table_name}"
        if ts_code:
            sql += f" WHERE ts_code = ?"
            result = self.conn.execute(sql, [ts_code]).fetchone()
        else:
            result = self.conn.execute(sql).fetchone()
        return result[0] if result and result[0] else None

    def get_max_date_col(
        self, table_name: str, date_col: str, where: str = ""
    ) -> date | None:
        """获取表中指定日期列的最大值.

        适用于日期列名非 trade_date 的表（如 trade_cal 的 cal_date）。
        Args:
            table_name: 表名.
            date_col: 日期列名.
            where: 可选 WHERE 子句（不含 WHERE 关键字），如 "exchange='SSE'".
        """
        if not self.table_exists(table_name):
            return None
        sql = f"SELECT MAX({date_col}) FROM {table_name}"
        if where:
            sql += f" WHERE {where}"
        result = self.conn.execute(sql).fetchone()
        return result[0] if result and result[0] else None

    def count_rows(self, table_name: str) -> int:
        """统计表行数."""
        if not self.table_exists(table_name):
            return 0
        result = self.conn.execute(f"SELECT count(*) FROM {table_name}").fetchone()
        return result[0] if result else 0

    def get_existing_dates(
        self, table_name: str, ts_code: str, start: str | date, end: str | date
    ) -> set[date]:
        """获取某标的在指定日期范围内已存在的交易日集合（用于查漏补缺）."""
        if not self.table_exists(table_name):
            return set()
        sql = (
            f"SELECT trade_date FROM {table_name} "
            f"WHERE ts_code = ? AND trade_date BETWEEN ? AND ?"
        )
        rows = self.conn.execute(
            sql, [ts_code, self._to_date(start), self._to_date(end)]
        ).fetchall()
        return {r[0] for r in rows}

    @staticmethod
    def _to_date(d: str | date | datetime) -> date:
        if isinstance(d, datetime):
            return d.date()
        if isinstance(d, str):
            return datetime.strptime(d.replace("-", ""), "%Y%m%d").date()
        return d

    # ------------------------------------------------------------------
    # trade_cal 专用查询
    # ------------------------------------------------------------------

    def get_trade_dates(
        self,
        exchange: str = "SSE",
        start: str | date | None = None,
        end: str | date | None = None,
        is_open: bool | None = True,
    ) -> list[date]:
        """获取交易日历中的交易日列表.

        Args:
            exchange: 交易所代码 (SSE/SZSE/CFFEX 等)，默认上交所.
            start: 起始日期 (str YYYYMMDD 或 date)，None 表示不设下限.
            end: 结束日期，None 表示不设上限.
            is_open: True=仅交易日, False=仅休市日, None=全部.

        Returns:
            按日期升序排列的日期列表.
        """
        if not self.table_exists("trade_cal"):
            return []
        sql = "SELECT cal_date FROM trade_cal WHERE exchange = ?"
        params: list[Any] = [exchange]
        if start is not None:
            sql += " AND cal_date >= ?"
            params.append(self._to_date(start))
        if end is not None:
            sql += " AND cal_date <= ?"
            params.append(self._to_date(end))
        if is_open is not None:
            sql += " AND is_open = ?"
            params.append(is_open)
        sql += " ORDER BY cal_date"
        rows = self.conn.execute(sql, params).fetchall()
        return [r[0] for r in rows]

    def get_cal_date_range(self, exchange: str) -> tuple[date | None, date | None]:
        """获取指定交易所 trade_cal 的 (min_cal_date, max_cal_date).

        Returns:
            (min, max) 元组；空表时返回 (None, None).
        """
        if not self.table_exists("trade_cal"):
            return (None, None)
        row = self.conn.execute(
            "SELECT MIN(cal_date), MAX(cal_date) FROM trade_cal WHERE exchange = ?",
            [exchange],
        ).fetchone()
        if not row or not row[0]:
            return (None, None)
        return (row[0], row[1])

    # ------------------------------------------------------------------
    # 表列表 & 元信息
    # ------------------------------------------------------------------

    def list_tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main' ORDER BY table_name;"
        ).fetchall()
        return [r[0] for r in rows]

    def _get_table_columns(self, table_name: str) -> list[str]:
        """获取表的列名列表（按 schema 顺序）."""
        rows = self.conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='main' AND table_name=? "
            "ORDER BY ordinal_position",
            [table_name],
        ).fetchall()
        return [r[0] for r in rows]

    def get_table_columns(self, table_name: str) -> list[str]:
        """获取表的列名列表（公开 API）."""
        if not self.table_exists(table_name):
            return []
        return self._get_table_columns(table_name)
