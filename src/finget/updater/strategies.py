"""数据更新策略 — 全量/增量/查漏补缺."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta

import pandas as pd

from finget.config import Config, DatasetConfig, UpdateConfig
from finget.fetchers.base import BaseFetcher
from finget.fetchers.progress import create_progress, iter_with_progress
from finget.logging import log
from finget.storage.duckdb_store import (
    BROKER_DATASETS,
    CALENDAR_DATASETS,
    HK_US_BASIC_DATASETS,
    SURVEY_DATASETS,
    THS_INDEX_DATASETS,
    TIME_SERIES_DATASETS,
    DuckDBStore,
)

# 研报类数据集（不分 ts_code 整体拉取，按 start_date/end_date 区间）
RESEARCH_DATASETS = {"report_rc"}

# 研报拉取的按天/按季度切换阈值（自然日）。
# 跨度 ≤ 此值时按天逐日拉取（规避镜像站大 offset 分页限制）；
# 跨度 > 此值时按季度切分拉取（每季约 2~4 万条，offset 不会太大）。
RESEARCH_DAILY_THRESHOLD_DAYS = 60


def _to_yyyymmdd_str(d: str | date | datetime) -> str:
    """将 str/date/datetime 归一化为 YYYYMMDD 字符串（tushare 入参格式）."""
    if isinstance(d, str):
        return d.replace("-", "")
    if isinstance(d, datetime):
        return d.strftime("%Y%m%d")
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    raise TypeError(f"Unsupported date type: {type(d)}")


def _to_date(d: str | date | datetime) -> date:
    """将 str/date/datetime 归一化为 date 对象."""
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str):
        return datetime.strptime(d.replace("-", ""), "%Y%m%d").date()
    raise TypeError(f"Unsupported date type: {type(d)}")


class UpdateStrategy:
    """数据更新策略管理器.

    协调 fetcher 与 store，根据 dataset.type 自动分流到对应的拉取行为。
    """

    def __init__(
        self,
        fetcher: BaseFetcher,
        store: DuckDBStore,
        config: Config,
    ) -> None:
        self.fetcher = fetcher
        self.store = store
        self.config = config
        self.update_cfg: UpdateConfig = config.update

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(
        self,
        dataset: DatasetConfig,
        ts_codes: list[str] | None = None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """执行数据拉取/更新.

        根据 dataset.type 自动分流到对应的拉取行为，调用方无需关心"模式"概念：
        - stock_basic: 一次性全量拉取
        - trade_cal: 按交易所一次性拉取（start_date/end_date 限定范围）
        - report_rc: 短跨度按天 / 长跨度按季度拉取
        - broker_recommend: 按月份拉取
        - daily_supported 时序表（无 ts_codes）: 按 trade_date 单日全市场拉取（极速）
        - 其他时序表（weekly / 指定 ts_codes）: 逐标的拉取，日期范围由 start_date/end_date 决定；
          start_date 为 None 时自动查 max_date 回溯（增量），max_date 也为 None 时回溯 N 年（全量）

        Args:
            dataset: 数据集配置.
            ts_codes: 指定标的列表; None 则自动获取全部.
            start_date: 起始日期 (str YYYYMMDD 或 date). None 时由行为自动决定.
            end_date: 结束日期. None 表示到今天.

        Returns:
            本次更新的总行数.
        """
        if not dataset.enabled:
            log.warning(f"Dataset '{dataset.name}' is disabled, skip.")
            return 0

        log.info(f"Updating dataset '{dataset.name}'")

        # 确保表已创建
        if not self.store.table_exists(dataset.name):
            self.store.init_table(dataset.type, dataset.name)

        if dataset.type == "stock_basic":
            return self._update_stock_basic(dataset)
        elif dataset.type in HK_US_BASIC_DATASETS:
            return self._update_hk_us_basic(dataset)
        elif dataset.type in CALENDAR_DATASETS:
            return self._update_calendar(dataset, start_date, end_date)
        elif dataset.type in RESEARCH_DATASETS:
            return self._update_research(dataset, start_date, end_date)
        elif dataset.type in BROKER_DATASETS:
            return self._update_broker(dataset, start_date, end_date)
        elif dataset.type in SURVEY_DATASETS:
            return self._update_survey(dataset, ts_codes, start_date, end_date)
        elif dataset.type in THS_INDEX_DATASETS:
            return self._update_ths_index(dataset)
        elif dataset.type in TIME_SERIES_DATASETS:
            if dataset.daily_supported and not ts_codes:
                return self._update_by_date(dataset, start_date, end_date)
            return self._update_time_series(
                dataset, ts_codes, start_date, end_date
            )
        else:
            log.warning(f"Unknown dataset type '{dataset.type}', skip.")
            return 0

    def run_scan(
        self,
        dataset: DatasetConfig,
        ts_codes: list[str] | None = None,
    ) -> int:
        """查漏补缺：对比交易日历找出缺失的交易日数据并补齐.

        仅对时序数据集（TIME_SERIES_DATASETS）生效。
        依赖 trade_cal 表已有数据；完全空表的标的会被跳过（需先拉取初始化数据）。

        Args:
            dataset: 数据集配置.
            ts_codes: 指定标的列表; None 则自动获取全部.

        Returns:
            补齐的总行数.
        """
        if not dataset.enabled:
            log.warning(f"Dataset '{dataset.name}' is disabled, skip.")
            return 0

        log.info(f"Scanning dataset '{dataset.name}' for missing dates")

        # 确保表已创建
        if not self.store.table_exists(dataset.name):
            self.store.init_table(dataset.type, dataset.name)

        if dataset.type not in TIME_SERIES_DATASETS:
            log.warning(
                f"Dataset '{dataset.name}' (type={dataset.type}) is not a time-series "
                f"dataset, scan is only applicable to TIME_SERIES_DATASETS."
            )
            return 0

        if ts_codes is None:
            ts_codes = self._get_all_ts_codes()
        return self._scan_and_fill(dataset, ts_codes)

    # ------------------------------------------------------------------
    # 基础信息表
    # ------------------------------------------------------------------

    def _update_stock_basic(self, dataset: DatasetConfig) -> int:
        """更新股票基础信息（全量覆盖式 upsert）."""
        df = self.fetcher.fetch_all(
            api_name=dataset.api_name,
            params=dataset.params,
            page_size=self.fetcher.page_size if hasattr(self.fetcher, "page_size") else 5000,
        )
        if df.empty:
            log.warning(f"{dataset.name}: no data fetched.")
            return 0
        # 日期格式转换
        if "list_date" in df.columns:
            df["list_date"] = pd.to_datetime(df["list_date"], format="%Y%m%d", errors="coerce").dt.date
        if "delist_date" in df.columns:
            df["delist_date"] = pd.to_datetime(df["delist_date"], format="%Y%m%d", errors="coerce").dt.date

        n = self.store.upsert(dataset.name, df, conflict_keys=["ts_code"])
        log.info(f"{dataset.name}: upserted {n} rows")
        return n

    # ------------------------------------------------------------------
    # 港美股基础信息（hk_basic + us_basic 合并写入）
    # ------------------------------------------------------------------

    def _update_hk_us_basic(self, dataset: DatasetConfig) -> int:
        """更新港美股基础信息（港股 hk_basic + 美股 us_basic 合并写入一张表）.

        分别调两个接口拉取，合并后按 ts_code 幂等写入。
        两接口输出对齐到 (ts_code, name, enname) 三列，多余列被 upsert 的
        schema 鲁棒性自动丢弃。

        - hk_basic: 港股列表，单次全量（params: list_status=L）
        - us_basic: 美股列表，分页提取（fetch_all 自动分页，page_size=6000）
        """
        page_size = self.fetcher.page_size if hasattr(self.fetcher, "page_size") else 5000
        frames: list[pd.DataFrame] = []

        # 1. 港股
        try:
            hk_df = self.fetcher.fetch_all(
                api_name="hk_basic",
                params={"list_status": "L"},
                page_size=page_size,
            )
            if not hk_df.empty:
                frames.append(hk_df)
                log.info(f"{dataset.name}: hk_basic fetched {len(hk_df)} rows")
        except Exception as e:
            log.error(f"{dataset.name}: hk_basic failed: {e}")

        # 2. 美股（单次最大 6000，fetch_all 自动分页）
        try:
            us_df = self.fetcher.fetch_all(
                api_name="us_basic",
                params={},
                page_size=6000,
            )
            if not us_df.empty:
                frames.append(us_df)
                log.info(f"{dataset.name}: us_basic fetched {len(us_df)} rows")
        except Exception as e:
            log.error(f"{dataset.name}: us_basic failed: {e}")

        if not frames:
            log.warning(f"{dataset.name}: no data fetched from hk_basic/us_basic.")
            return 0

        merged = pd.concat(frames, ignore_index=True)
        n = self.store.upsert(dataset.name, merged, conflict_keys=["ts_code"])
        log.info(f"{dataset.name}: upserted {n} rows (hk + us merged)")
        return n

    # ------------------------------------------------------------------
    # 同花顺概念/行业成分股（ths_index + ths_sector + ths_member 合并）
    # ------------------------------------------------------------------

    def _update_ths_index(self, dataset: DatasetConfig) -> int:
        """更新同花顺概念/行业成分股.

        合并三个 tushare API：
        1. ths_index (doc 259) — 概念板块分类（code, name, type）
        2. ths_sector (doc 260) — 行业板块分类
        3. ths_member (doc 261) — 概念板块成分股（ts_code, name → concept_code）

        最终合并为一张表 (ts_code, name, index_code, index_name, index_type, src).
        每个 (ts_code, index_code) 唯一。
        """
        page_size = 5000
        frames: list[pd.DataFrame] = []

        # --- 1. ths_index：概念分类 ---
        try:
            idx_df = self.fetcher.fetch_all(
                api_name="ths_index",
                params={},
                page_size=page_size,
            )
            if not idx_df.empty:
                idx_df["src"] = "index"
                # 适配列名: code→index_code, name→index_name, type→index_type
                idx_df = idx_df.rename(columns={
                    "code": "index_code",
                    "name": "index_name",
                    "type": "index_type",
                })
                # ths_index 没有 ts_code（它只是分类列表），暂时留空
                frames.append(idx_df)
                log.info(f"{dataset.name}: ths_index fetched {len(idx_df)} concepts")
        except Exception as e:
            log.warning(f"{dataset.name}: ths_index failed: {e}")

        # --- 2. ths_sector：行业分类（结构与 ths_index 类似） ---
        try:
            sec_df = self.fetcher.fetch_all(
                api_name="ths_sector",
                params={},
                page_size=page_size,
            )
            if not sec_df.empty:
                sec_df["src"] = "sector"
                sec_df = sec_df.rename(columns={
                    "code": "index_code",
                    "name": "index_name",
                    "type": "index_type",
                })
                frames.append(sec_df)
                log.info(f"{dataset.name}: ths_sector fetched {len(sec_df)} sectors")
        except Exception as e:
            log.warning(f"{dataset.name}: ths_sector failed: {e}")

        # --- 3. ths_member：成分股映射 ---
        member_rows: list[pd.DataFrame] = []
        try:
            member_df = self.fetcher.fetch_all(
                api_name="ths_member",
                params={},
                page_size=page_size,
            )
            if not member_df.empty:
                # ths_member 列: ts_code, name(股票名), con_code, con_name(概念名)
                member_df["src"] = "member"
                member_df = member_df.rename(columns={
                    "con_code": "index_code",
                    "con_name": "index_name",
                })
                member_rows.append(member_df)
                log.info(f"{dataset.name}: ths_member fetched {len(member_df)} rows")
        except Exception as e:
            log.warning(f"{dataset.name}: ths_member failed: {e}")

        # --- 合并 ---
        # 策略：从 ths_index / ths_sector 拿到所有概念/行业的 (code, name, type)
        # 然后与 ths_member 的 (ts_code, concept_code) 做 JOIN
        # 最终表: (ts_code, stock_name, index_code, index_name, index_type, src)

        if not member_rows:
            log.warning(f"{dataset.name}: no member data")
            return 0

        members = pd.concat(member_rows, ignore_index=True)

        # 确保必要列存在
        for col in ["ts_code", "index_code"]:
            if col not in members.columns:
                log.warning(f"{dataset.name}: missing column '{col}' in member data")
                return 0

        # 如果 member 没有 index_name（con_name 已重命名），尝试从分类 lookup 补全
        if frames and "index_name" not in members.columns:
            lookup = pd.concat(frames, ignore_index=True)
            if "index_code" in lookup.columns and "index_name" in lookup.columns:
                members = members.merge(
                    lookup[["index_code", "index_name", "index_type"]],
                    on="index_code", how="left", suffixes=("", "_cls"),
                )
                # 用分类表的 index_name 补全
                if "index_name_cls" in members.columns:
                    members["index_name"] = members["index_name"].fillna(members["index_name_cls"]) if "index_name" in members.columns else members["index_name_cls"]
                    members = members.drop(columns=["index_name_cls"])
                if "index_type_cls" in members.columns:
                    members["index_type"] = members["index_type"].fillna(members["index_type_cls"]) if "index_type" in members.columns else members["index_type_cls"]
                    members = members.drop(columns=["index_type_cls"])

        # 确保 src 列
        if "src" not in members.columns:
            members["src"] = "member"

        n = self.store.upsert(dataset.name, members, conflict_keys=["ts_code", "index_code"])
        log.info(f"{dataset.name}: upserted {n} rows")
        return n

    # ------------------------------------------------------------------
    # 日历类数据
    # ------------------------------------------------------------------

    def _update_calendar(
        self,
        dataset: DatasetConfig,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """更新日历类数据集（如 trade_cal）.

        整体一次拉取，按 (exchange, cal_date) 幂等写入。
        start_date 未指定时，根据已有最大日期回溯 incremental_lookback_days 天（增量补齐）。

        Args:
            start_date: 显式起始日期. 若提供，优先级最高（覆盖增量回溯逻辑）.
            end_date: 显式结束日期. 若提供，覆盖默认的今天.
        """
        params = dict(dataset.params)

        # 外部传入的 end_date 优先级最高
        if end_date is not None:
            params.setdefault("end_date", _to_yyyymmdd_str(end_date))

        if start_date is not None:
            # 外部显式指定：直接使用
            params.setdefault("start_date", _to_yyyymmdd_str(start_date))
        else:
            # 未指定起始日期：查最大 cal_date，回溯 incremental_lookback_days 天（增量）
            where = ""
            if "exchange" in params:
                where = f"exchange = '{params['exchange']}'"
            max_d = self.store.get_max_date_col(dataset.name, "cal_date", where=where)
            if max_d is not None:
                start = max_d - timedelta(days=self.update_cfg.incremental_lookback_days)
                params.setdefault("start_date", start.strftime("%Y%m%d"))

        df = self.fetcher.fetch_all(
            api_name=dataset.api_name,
            params=params,
            page_size=self.fetcher.page_size if hasattr(self.fetcher, "page_size") else 5000,
        )
        if df.empty:
            log.warning(f"{dataset.name}: no data fetched.")
            return 0

        # 转换日期列
        if "cal_date" in df.columns:
            df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d", errors="coerce").dt.date
        if "pretrade_date" in df.columns:
            df["pretrade_date"] = pd.to_datetime(df["pretrade_date"], format="%Y%m%d", errors="coerce").dt.date
        # is_open: 0/1 → bool
        if "is_open" in df.columns and df["is_open"].dtype != bool:
            df["is_open"] = df["is_open"].astype(int).astype(bool)

        n = self.store.upsert(dataset.name, df, conflict_keys=["exchange", "cal_date"])
        log.info(f"{dataset.name}: upserted {n} rows")
        return n

    # ------------------------------------------------------------------
    # 研报类数据
    # ------------------------------------------------------------------

    def _update_research(
        self,
        dataset: DatasetConfig,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """更新研报类数据集（如 report_rc）.

        短跨度（≤ RESEARCH_DAILY_THRESHOLD_DAYS）按天逐日拉取，长跨度按季度分批并发拉取
        （避免镜像站不支持大 offset 分页查询），每批并发拉取后合并写入一次 DB，
        按 5 列联合冲突键 (ts_code, report_date, org_name, author_name, quarter) 幂等写入。

        start_date 未指定时，根据已有最大 report_date 回溯 incremental_lookback_days 天（增量），
        表为空时回溯 full_lookback_years 年（全量）。

        Args:
            start_date: 显式起始日期. 若提供，优先级最高.
            end_date: 显式结束日期. 若提供，覆盖默认的今天.
        """
        # 1. 计算总体日期范围
        if end_date is not None:
            overall_end = _to_date(end_date)
        else:
            overall_end = date.today()

        if start_date is not None:
            overall_start = _to_date(start_date)
        else:
            # 未指定起始日期：查最大 report_date 回溯（增量），表空则回溯 N 年（全量）
            max_d = self.store.get_max_date_col(dataset.name, "report_date")
            if max_d is not None:
                overall_start = max_d - timedelta(days=self.update_cfg.incremental_lookback_days)
            else:
                overall_start = date.today() - timedelta(days=365 * self.update_cfg.full_lookback_years)

        log.info(
            f"{dataset.name}: fetching {overall_start.isoformat()} "
            f"~ {overall_end.isoformat()} (concurrent)"
        )

        # 2. 批次切分：短跨度按天逐日拉取（规避镜像站大 offset 分页限制），
        #    长跨度按季度切分拉取（每季约 2~4 万条，分页 offset 不会太大）
        span_days = (overall_end - overall_start).days
        if span_days <= RESEARCH_DAILY_THRESHOLD_DAYS:
            # 按天：逐自然日生成单日区间（report_rc 非交易日也有研报，不能用交易日历过滤）
            batches: list[tuple[date, date]] = [
                (overall_start + timedelta(days=i), overall_start + timedelta(days=i))
                for i in range(span_days + 1)
            ]
            batch_strategy = "按天"
        else:
            batches = self._generate_quarters(overall_start, overall_end)
            batch_strategy = "按季"

        # 3. 并发拉取 + 批量合并写入
        #    每 concurrent_chunk 个批次并发拉取后，合并写入一次 DB（减少事务开销）
        from finget.fetchers.tushare_fetcher import TushareFetcher

        is_tushare = isinstance(self.fetcher, TushareFetcher)
        concurrent_chunk = 3 if is_tushare else 1  # TushareFetcher 支持并发，其他串行
        total_rows = 0

        with create_progress() as progress:
            task = progress.add_task(
                f"[{dataset.name}] 研报{batch_strategy} {overall_start.isoformat()}~{overall_end.isoformat()}",
                total=len(batches)
            )
            for i in range(0, len(batches), concurrent_chunk):
                chunk_batches = batches[i : i + concurrent_chunk]

                # 并发拉取批次数据
                if is_tushare and len(chunk_batches) > 1:
                    batch_frames: dict[tuple[date, date], pd.DataFrame] = {}
                    with ThreadPoolExecutor(max_workers=min(3, len(chunk_batches))) as executor:
                        futures = {
                            executor.submit(
                                self._fetch_research_batch_df, dataset, b_start, b_end
                            ): (b_start, b_end)
                            for b_start, b_end in chunk_batches
                        }
                        for future in as_completed(futures):
                            b_key = futures[future]
                            try:
                                batch_frames[b_key] = future.result()
                            except Exception as e:
                                log.error(f"{dataset.name} {b_key[0]}~{b_key[1]}: {e}")
                                batch_frames[b_key] = pd.DataFrame()
                else:
                    # 串行拉取（非 TushareFetcher 或单批次）
                    batch_frames = {}
                    for b_start, b_end in chunk_batches:
                        try:
                            batch_frames[(b_start, b_end)] = self._fetch_research_batch_df(
                                dataset, b_start, b_end
                            )
                        except Exception as e:
                            log.error(f"{dataset.name} {b_start}~{b_end}: {e}")
                            batch_frames[(b_start, b_end)] = pd.DataFrame()

                # 合并写入
                non_empty_frames = [df for df in batch_frames.values() if not df.empty]
                if non_empty_frames:
                    merged = pd.concat(non_empty_frames, ignore_index=True)
                    try:
                        rows = self.store.upsert(
                            dataset.name, merged,
                            conflict_keys=["ts_code", "report_date", "org_name", "author_name", "quarter"],
                        )
                        total_rows += rows
                    except Exception as e:
                        log.error(f"{dataset.name} batch write: {e}")

                progress.advance(task, len(chunk_batches))

        log.info(f"{dataset.name}: total {total_rows} rows ({batch_strategy}, concurrent)")
        return total_rows

    def _fetch_research_batch_df(
        self,
        dataset: DatasetConfig,
        q_start: date,
        q_end: date,
    ) -> pd.DataFrame:
        """拉取一个季度的研报数据（不写入 DB，由上层合并写入）."""
        params = dict(dataset.params)
        params["start_date"] = _to_yyyymmdd_str(q_start)
        params["end_date"] = _to_yyyymmdd_str(q_end)

        df = self.fetcher.fetch_all(
            api_name=dataset.api_name,
            params=params,
            page_size=3000,  # report_rc 单次最大 3000 条
        )
        if df.empty:
            return df

        # 转换日期列
        if "report_date" in df.columns:
            df["report_date"] = pd.to_datetime(
                df["report_date"], format="%Y%m%d", errors="coerce"
            ).dt.date

        return df

    @staticmethod
    def _generate_quarters(start: date, end: date) -> list[tuple[date, date]]:
        """生成季度时间段列表.

        每个季度覆盖 3 个月：
        - Q1: 1月1日 ~ 3月31日
        - Q2: 4月1日 ~ 6月30日
        - Q3: 7月1日 ~ 9月30日
        - Q4: 10月1日 ~ 12月31日

        Returns:
            [(q_start, q_end), ...] 列表，保证覆盖 start ~ end 的全部日期.
        """
        quarters: list[tuple[date, date]] = []

        # 找到包含 start 的季度起始月
        month = start.month
        quarter_start_month = ((month - 1) // 3) * 3 + 1  # 1, 4, 7, 10
        cur_year = start.year
        cur_month = quarter_start_month

        while True:
            q_start = date(cur_year, cur_month, 1)
            q_end_month = cur_month + 2  # 季度末月
            # 季度末日期
            if q_end_month == 3:
                q_end = date(cur_year, 3, 31)
            elif q_end_month == 6:
                q_end = date(cur_year, 6, 30)
            elif q_end_month == 9:
                q_end = date(cur_year, 9, 30)
            elif q_end_month == 12:
                q_end = date(cur_year, 12, 31)
            else:
                # 不应到这里
                q_end = date(cur_year, cur_month + 3, 1) - timedelta(days=1)

            if q_start > end:
                break

            # 对齐到实际日期范围
            effective_start = max(q_start, start)
            effective_end = min(q_end, end)

            quarters.append((effective_start, effective_end))

            # 下一个季度
            cur_month += 3
            if cur_month > 12:
                cur_month = 1
                cur_year += 1

        return quarters

    # ------------------------------------------------------------------
    # 券商月度金股
    # ------------------------------------------------------------------

    def _update_broker(
        self,
        dataset: DatasetConfig,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """更新券商月度金股数据集（如 broker_recommend）.

        按月份并发拉取（入参 month=YYYYMM），每 3 个月并发拉取后合并写入一次 DB，
        按 (month, broker, ts_code) 幂等写入。

        start_date 未指定时，根据已有最大 month 回溯（增量），表为空时回溯 full_lookback_years 年（全量）。

        Args:
            start_date: 显式起始日期. 若提供，从该月开始拉取.
            end_date: 显式结束日期. 若提供，到该月为止.
        """
        # 1. 计算月份范围
        if end_date is not None:
            overall_end = _to_date(end_date)
        else:
            overall_end = date.today()

        if start_date is not None:
            overall_start = _to_date(start_date)
        else:
            # 未指定起始日期：查最大 month 回溯（增量），表空则回溯 N 年（全量）
            max_month_str = self._get_broker_max_month(dataset.name)
            if max_month_str is not None:
                y, m = int(max_month_str[:4]), int(max_month_str[4:])
                overall_start = date(y, m, 1)
            else:
                overall_start = date.today() - timedelta(days=365 * self.update_cfg.full_lookback_years)

        # 2. 生成月份列表（YYYYMM 格式）
        months = self._generate_months(overall_start, overall_end)

        log.info(
            f"{dataset.name}: fetching {months[0]}~{months[-1]} "
            f"(by month, concurrent)"
        )

        # 3. 并发拉取 + 批量合并写入
        from finget.fetchers.tushare_fetcher import TushareFetcher

        is_tushare = isinstance(self.fetcher, TushareFetcher)
        concurrent_chunk = 3 if is_tushare else 1
        total_rows = 0

        with create_progress() as progress:
            task = progress.add_task(
                f"[{dataset.name}] 券商金股 {months[0]}~{months[-1]}",
                total=len(months),
            )
            for i in range(0, len(months), concurrent_chunk):
                chunk_months = months[i : i + concurrent_chunk]

                # 并发拉取
                if is_tushare and len(chunk_months) > 1:
                    month_frames: dict[str, pd.DataFrame] = {}
                    with ThreadPoolExecutor(max_workers=min(3, len(chunk_months))) as executor:
                        futures = {
                            executor.submit(
                                self._fetch_broker_month_df, dataset, mm
                            ): mm
                            for mm in chunk_months
                        }
                        for future in as_completed(futures):
                            mm = futures[future]
                            try:
                                month_frames[mm] = future.result()
                            except Exception as e:
                                log.error(f"{dataset.name} {mm}: {e}")
                                month_frames[mm] = pd.DataFrame()
                else:
                    month_frames = {}
                    for mm in chunk_months:
                        try:
                            month_frames[mm] = self._fetch_broker_month_df(dataset, mm)
                        except Exception as e:
                            log.error(f"{dataset.name} {mm}: {e}")
                            month_frames[mm] = pd.DataFrame()

                # 合并写入
                non_empty_frames = [df for df in month_frames.values() if not df.empty]
                if non_empty_frames:
                    merged = pd.concat(non_empty_frames, ignore_index=True)
                    try:
                        rows = self.store.upsert(
                            dataset.name, merged,
                            conflict_keys=["month", "broker", "ts_code"],
                        )
                        total_rows += rows
                    except Exception as e:
                        log.error(f"{dataset.name} batch write: {e}")

                progress.advance(task, len(chunk_months))

        log.info(f"{dataset.name}: total {total_rows} rows (concurrent)")
        return total_rows

    def _fetch_broker_month_df(
        self,
        dataset: DatasetConfig,
        month: str,
    ) -> pd.DataFrame:
        """拉取单月券商金股数据（不写入 DB，由上层合并写入）."""
        params = dict(dataset.params)
        params["month"] = month

        df = self.fetcher.fetch_all(
            api_name=dataset.api_name,
            params=params,
            page_size=1000,  # broker_recommend 单次最大 1000 条
        )
        return df

    @staticmethod
    def _generate_months(start: date, end: date) -> list[str]:
        """生成月份列表（YYYYMM 格式）.

        从 start 所在月份到 end 所在月份，逐月生成。
        """
        months: list[str] = []
        y, m = start.year, start.month
        while True:
            months.append(f"{y}{m:02d}")
            if y == end.year and m == end.month:
                break
            m += 1
            if m > 12:
                m = 1
                y += 1
        return months

    def _get_broker_max_month(self, table_name: str) -> str | None:
        """获取表中最大 month 值（YYYYMM 格式）."""
        if not self.store.table_exists(table_name):
            return None
        result = self.store.conn.execute(
            f"SELECT MAX(month) FROM {table_name}"
        ).fetchone()
        return result[0] if result and result[0] else None

    # ------------------------------------------------------------------
    # 机构调研数据（逐标的拉取，content 大文本拆 detail 表）
    # ------------------------------------------------------------------

    def _update_survey(
        self,
        dataset: DatasetConfig,
        ts_codes: list[str] | None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """更新机构调研数据集（如 stk_surv）.

        逐标的拉取（单次最大 100 条，按 ts_code 分页）。
        拉到的数据拆分写入两张表：
        - stk_surv（主表，9 列元数据，不含 content 大文本）
        - stk_surv_detail（详情表，content 大文本隔离，按需 JOIN）

        日期范围逻辑：
        - start_date 显式指定 → 用之
        - start_date 为 None → 查 max(surv_date) 回溯 incremental_lookback_days 天（增量）；
          表空则回溯 full_lookback_years 年（全量初始化）
        - end_date 显式指定 → 用之；None → 今天

        Args:
            ts_codes: 指定标的列表; None 则自动获取全部.
            start_date: 起始日期.
            end_date: 结束日期.
        """
        # 确保详情表已创建
        detail_table = f"{dataset.name}_detail"
        if not self.store.table_exists(detail_table):
            self.store.init_table(detail_table, detail_table)

        # 1. 计算日期范围
        if end_date is not None:
            overall_end = _to_date(end_date)
        else:
            overall_end = date.today()

        if start_date is not None:
            overall_start = _to_date(start_date)
        else:
            # 未指定起始日期：查 max(surv_date) 回溯（增量），表空则回溯 N 年（全量）
            max_d = self.store.get_max_date_col(dataset.name, "surv_date")
            if max_d is not None:
                overall_start = max_d - timedelta(days=self.update_cfg.incremental_lookback_days)
            else:
                overall_start = date.today() - timedelta(days=365 * self.update_cfg.full_lookback_years)

        # 2. 获取标的列表
        if ts_codes is None:
            ts_codes = self._get_all_ts_codes()

        log.info(
            f"{dataset.name}: 逐标的拉取 {len(ts_codes)} 只标的, "
            f"{overall_start.isoformat()} ~ {overall_end.isoformat()}"
        )

        # 3. 逐标的拉取 + 拆分写入
        total_rows = 0
        for ts_code in iter_with_progress(
            ts_codes,
            description=f"[{dataset.name}] 逐标的拉取",
            total=len(ts_codes),
        ):
            try:
                rows = self._fetch_survey_one(
                    dataset, ts_code, overall_start, overall_end
                )
                total_rows += rows
            except Exception as e:
                log.error(f"{dataset.name} {ts_code}: {e}")
                continue

        log.info(f"{dataset.name}: total {total_rows} rows updated.")
        return total_rows

    def _fetch_survey_one(
        self,
        dataset: DatasetConfig,
        ts_code: str,
        start: date,
        end: date,
    ) -> int:
        """拉取单个标的的机构调研数据，拆分写入主表和详情表."""
        df = self.fetcher.fetch_all(
            api_name=dataset.api_name,
            params={
                **dataset.params,
                "ts_code": ts_code,
                "start_date": _to_yyyymmdd_str(start),
                "end_date": _to_yyyymmdd_str(end),
            },
            page_size=100,  # stk_surv 单次最大 100 条
        )
        if df.empty:
            return 0

        # 日期转换
        if "surv_date" in df.columns:
            df["surv_date"] = pd.to_datetime(
                df["surv_date"], format="%Y%m%d", errors="coerce"
            ).dt.date

        # 拆分：主表（不含 content）+ 详情表（content）
        detail_table = f"{dataset.name}_detail"
        conflict_keys = ["ts_code", "surv_date", "rece_org"]

        # 主表：drop content 列（如果存在）
        main_df = df.drop(columns=["content"], errors="ignore")
        n = self.store.upsert(dataset.name, main_df, conflict_keys=conflict_keys)

        # 详情表：只写 content 非空的行
        if "content" in df.columns:
            detail_df = df[["ts_code", "surv_date", "rece_org", "content"]].copy()
            detail_df = detail_df[detail_df["content"].notna() & (detail_df["content"] != "")]
            if not detail_df.empty:
                self.store.upsert(detail_table, detail_df, conflict_keys=conflict_keys)

        return n

    # ------------------------------------------------------------------
    # 时序数据（按 trade_date 单日全市场）
    # ------------------------------------------------------------------

    def _update_by_date(
        self,
        dataset: DatasetConfig,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """按 trade_date 单日全市场拉取并 upsert.

        适用于 daily / adj_factor / daily_basic 等 tushare 支持"按日全市场查询"的接口。
        相比逐标的 INCREMENTAL 模式，速度提升 ~5000 倍（5000 次 → 1 次 接口调用/日）。

        Args:
            dataset: 数据集配置（须 daily_supported=True）
            start_date: 起始日期. None 时：
                - 如果表里已有数据，从 (max_date + 1) 开始
                - 如果表为空，回溯 incremental_lookback_days 天
            end_date: 结束日期. None 时取今天.

        Returns:
            upsert 总行数.
        """
        # 1. 计算日期范围
        if end_date is not None:
            end = _to_date(end_date)
        else:
            end = date.today()

        if start_date is not None:
            start = _to_date(start_date)
        else:
            # 自动推断：表里有数据从 max+1 开始；空表回溯几天
            max_d = self.store.get_max_date_col(dataset.name, "trade_date")
            if max_d is not None:
                start = max_d + timedelta(days=1)
                if start > end:
                    log.info(f"{dataset.name}: already up-to-date (max={max_d}), skip.")
                    return 0
            else:
                # 空表：回溯 5 天兜底
                start = end - timedelta(days=self.update_cfg.incremental_lookback_days)

        if start > end:
            log.info(f"{dataset.name}: start ({start}) > end ({end}), skip.")
            return 0

        log.info(f"{dataset.name}: 按日全市场拉取 {start} ~ {end}")

        # 2. 获取交易日历（用于跳过非交易日 + 增量范围的合理性检查）
        from finget.fetchers.tushare_fetcher import TushareFetcher

        trade_cal: set[date] | None = None
        if isinstance(self.fetcher, TushareFetcher):
            trade_cal = self._get_trade_calendar()

        # 3. 并发拉取 + 批量合并写入
        #    使用 fetch_concurrent 并发拉取多天数据（线程池绕过串行等待），
        #    每 fetch_chunk 天并发拉取后，合并写入一次 DB（减少事务开销）。
        total_rows = 0
        cur = start
        days: list[date] = []
        while cur <= end:
            if trade_cal is None or cur in trade_cal:
                days.append(cur)
            cur += timedelta(days=1)

        # stk_factor_pro 单次最多 10000 条，其他接口默认 5000
        page_size = 10000 if dataset.api_name == "stk_factor_pro" else (
            self.fetcher.page_size if hasattr(self.fetcher, "page_size") else 5000
        )

        fetch_chunk = 5  # 每 5 天并发拉取一批
        from finget.fetchers.progress import create_progress
        from finget.fetchers.tushare_fetcher import TushareFetcher

        with create_progress() as progress:
            task = progress.add_task(
                f"[{dataset.name}] 按日拉取 {start}~{end}", total=len(days)
            )
            for i in range(0, len(days), fetch_chunk):
                chunk_days = days[i : i + fetch_chunk]

                # 并发拉取（TushareFetcher 才支持 fetch_concurrent）
                if isinstance(self.fetcher, TushareFetcher):
                    day_frames = self.fetcher.fetch_concurrent(
                        api_name=dataset.api_name,
                        trade_dates=chunk_days,
                        params=dataset.params,
                        page_size=page_size,
                        max_workers=min(3, len(chunk_days)),
                    )
                else:
                    # 非 TushareFetcher 回退到串行
                    day_frames = {}
                    for td in chunk_days:
                        try:
                            df = self.fetcher.fetch_all(
                                api_name=dataset.api_name,
                                params={**dataset.params, "trade_date": _to_yyyymmdd_str(td)},
                                page_size=page_size,
                            )
                            day_frames[td] = df
                        except Exception as e:
                            log.error(f"{dataset.name} {td}: {e}")
                            day_frames[td] = pd.DataFrame()

                # 转换日期列 + 过滤空数据
                non_empty_frames: list[pd.DataFrame] = []
                for td, df in day_frames.items():
                    if df.empty:
                        log.debug(f"{dataset.name} {td}: empty (可能非交易日)")
                        continue
                    if "trade_date" in df.columns:
                        df["trade_date"] = pd.to_datetime(
                            df["trade_date"], format="%Y%m%d", errors="coerce"
                        ).dt.date
                    non_empty_frames.append(df)

                # 合并写入
                if non_empty_frames:
                    merged = pd.concat(non_empty_frames, ignore_index=True)
                    try:
                        rows = self.store.upsert(dataset.name, merged)
                        total_rows += rows
                    except Exception as e:
                        log.error(f"{dataset.name} batch write: {e}")

                progress.advance(task, len(chunk_days))

        log.info(f"{dataset.name} (DAILY): total {total_rows} rows over {len(days)} day(s).")
        return total_rows

    # ------------------------------------------------------------------
    # 时序数据
    # ------------------------------------------------------------------

    def _update_time_series(
        self,
        dataset: DatasetConfig,
        ts_codes: list[str] | None,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """更新时序数据（K线、指标等）— 逐标的拉取.

        日期范围逻辑（对每个标的独立计算）：
        - start_date 显式指定 → 用之（覆盖一切默认）
        - start_date 为 None → 查该标的的 max_date 回溯 incremental_lookback_days 天（增量）；
          max_date 也为 None → 回溯 full_lookback_years 年（全量初始化）
        - end_date 显式指定 → 用之；None → 今天

        Args:
            start_date: 显式起始日期. None 时按 max_date 回溯或回溯 N 年.
            end_date: 显式结束日期. 默认今天.
        """
        if ts_codes is None:
            ts_codes = self._get_all_ts_codes()

        total_rows = 0

        for ts_code in iter_with_progress(
            ts_codes,
            description=f"[{dataset.name}] 逐标的拉取",
            total=len(ts_codes),
        ):
            try:
                rows = self._fetch_one(
                    dataset, ts_code, start_date, end_date
                )
                total_rows += rows
            except Exception as e:
                log.error(f"{dataset.name} {ts_code}: {e}")
                continue

        log.info(f"{dataset.name}: total {total_rows} rows updated.")
        return total_rows

    def _fetch_one(
        self,
        dataset: DatasetConfig,
        ts_code: str,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
    ) -> int:
        """获取单个标的数据.

        日期范围逻辑：
        - end_date 显式指定 → 用之；None → 今天
        - start_date 显式指定 → 用之（覆盖一切默认）
        - start_date 为 None → 查该标的 max_date 回溯 incremental_lookback_days 天（增量）；
          max_date 也为 None → 回溯 full_lookback_years 年（全量初始化）
        """
        # end_date
        if end_date is not None:
            end = _to_date(end_date)
        else:
            end = date.today()
        # start_date
        if start_date is not None:
            start = _to_date(start_date)
        else:
            max_date = self.store.get_max_date(dataset.name, ts_code)
            if max_date is None:
                # 表中无此标的，做全量（回溯 N 年）
                start = self._full_date_range()[0]
            else:
                start = max_date - timedelta(
                    days=self.update_cfg.incremental_lookback_days
                )

        # stk_factor_pro 单次最多 10000 条，其他接口默认 5000
        page_size = 10000 if dataset.api_name == "stk_factor_pro" else (
            self.fetcher.page_size if hasattr(self.fetcher, "page_size") else 5000
        )
        df = self.fetcher.fetch_all(
            api_name=dataset.api_name,
            params={**dataset.params, "ts_code": ts_code},
            start_date=start,
            end_date=end,
            page_size=page_size,
        )
        if df.empty:
            return 0

        # 转换日期列
        if "trade_date" in df.columns:
            df["trade_date"] = pd.to_datetime(df["trade_date"], format="%Y%m%d", errors="coerce").dt.date

        return self.store.upsert(dataset.name, df)

    # ------------------------------------------------------------------
    # 查漏补缺
    # ------------------------------------------------------------------

    def _scan_and_fill(self, dataset: DatasetConfig, ts_codes: list[str]) -> int:
        """扫描数据库中缺失的交易日数据并自动补齐.

        策略:
            1. 对每个标的，获取 [list_date, today] 范围内的预期交易日历.
            2. 与数据库已有日期比对，找出缺失.
            3. 分批拉取缺失日期的数据.
        """
        from finget.fetchers.tushare_fetcher import TushareFetcher

        total_filled = 0
        scanned = 0
        filled_count = 0  # 有缺失并补齐的标的数
        # 获取交易日历
        if isinstance(self.fetcher, TushareFetcher):
            trade_cal = self._get_trade_calendar()
        else:
            trade_cal = None

        batch_size = self.update_cfg.scan_batch_size

        # 用手动进度条，实时更新描述显示当前标的 + 累计补齐数
        # （不用 log.info 逐标的输出，避免与进度条交叉导致显示混乱）
        with create_progress() as progress:
            task = progress.add_task(
                f"[{dataset.name}] 查漏补缺", total=len(ts_codes)
            )
            for ts_code in ts_codes:
                progress.update(
                    task, description=f"[{dataset.name}] 查漏补缺 {ts_code}"
                )
                existing = self._get_existing_dates_set(dataset.name, ts_code)
                min_d = self.store.get_min_date(dataset.name, ts_code)
                max_d = self.store.get_max_date(dataset.name, ts_code)
                if min_d is None or max_d is None:
                    # 完全没有数据，跳过（应由 full 模式处理）
                    progress.advance(task)
                    scanned += 1
                    continue

                if trade_cal is not None:
                    expected = {d for d in trade_cal if min_d <= d <= max_d}
                else:
                    expected = set()  # 无法获取日历则跳过

                missing = sorted(expected - existing)
                if not missing:
                    progress.advance(task)
                    scanned += 1
                    continue

                progress.update(
                    task,
                    description=(
                        f"[{dataset.name}] 查漏补缺 {ts_code} "
                        f"({len(missing)} 缺失)"
                    ),
                )
                log.debug(f"{ts_code}: {len(missing)} missing dates, filling...")
                filled = self._fill_missing(dataset, ts_code, missing, batch_size)
                total_filled += filled
                if filled > 0:
                    filled_count += 1
                progress.advance(task)
                scanned += 1

        log.info(
            f"{dataset.name} scan: scanned {scanned} codes, "
            f"filled {filled_count} codes / {total_filled} rows total."
        )
        return total_filled

    def _fill_missing(
        self,
        dataset: DatasetConfig,
        ts_code: str,
        missing_dates: list[date],
        batch_size: int,
    ) -> int:
        """分批补齐缺失日期数据."""
        total = 0
        for i in range(0, len(missing_dates), batch_size):
            batch = missing_dates[i : i + batch_size]
            start = batch[0]
            end = batch[-1]
            df = self.fetcher.fetch_all(
                api_name=dataset.api_name,
                params={**dataset.params, "ts_code": ts_code},
                start_date=start,
                end_date=end,
                page_size=self.fetcher.page_size if hasattr(self.fetcher, "page_size") else 5000,
            )
            if df.empty:
                continue
            if "trade_date" in df.columns:
                df["trade_date"] = pd.to_datetime(
                    df["trade_date"], format="%Y%m%d", errors="coerce"
                ).dt.date
            total += self.store.upsert(dataset.name, df)
        return total

    def _get_existing_dates_set(self, table_name: str, ts_code: str) -> set[date]:
        min_d = self.store.get_min_date(table_name, ts_code)
        max_d = self.store.get_max_date(table_name, ts_code)
        if min_d is None or max_d is None:
            return set()
        return self.store.get_existing_dates(table_name, ts_code, min_d, max_d)

    def _get_trade_calendar(self) -> set[date]:
        """获取交易日历（开放日期集合）.

        优先从已建好的 trade_cal 表读取（SSE 交易所），
        若表不存在则回退到 tushare 实时拉取。
        """
        if self.store.table_exists("trade_cal"):
            return set(self.store.get_trade_dates(exchange="SSE", is_open=True))
        # 回退：从 tushare 拉取
        try:
            df = self.fetcher.fetch_all(
                api_name="trade_cal",
                params={"exchange": "SSE"},
                page_size=self.fetcher.page_size if hasattr(self.fetcher, "page_size") else 5000,
            )
            if df.empty:
                return set()
            df["cal_date"] = pd.to_datetime(df["cal_date"], format="%Y%m%d").dt.date
            open_dates = df[df["is_open"] == 1]["cal_date"]
            return set(open_dates.tolist())
        except Exception as e:
            log.warning(f"Failed to fetch trade calendar: {e}")
            return set()

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _get_all_ts_codes(self) -> list[str]:
        """从 stock_basic 表获取全部标的代码."""
        if not self.store.table_exists("stock_basic"):
            log.warning("stock_basic table not found, fetching first.")
            ds = next(
                (d for d in self.config.datasets if d.type == "stock_basic"),
                DatasetConfig(name="stock_basic", type="stock_basic", api_name="stock_basic"),
            )
            self._update_stock_basic(ds)
        df = self.store.query("SELECT ts_code FROM stock_basic ORDER BY ts_code;")
        return df["ts_code"].tolist() if not df.empty else []

    def _full_date_range(self) -> tuple[date, date]:
        """全量模式日期范围."""
        end = date.today()
        start = end - timedelta(days=365 * self.update_cfg.full_lookback_years)
        return start, end
