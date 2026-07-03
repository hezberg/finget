"""配置管理 — 极简版.

设计原则：
- 所有"基本不变"的配置（数据库路径、镜像站、限速、lookback、数据集列表等）
  都 hardcode 在 Pydantic 模型默认值中，用户无需关心。
- TUSHARE_TOKEN 从 .env / 环境变量读取。
- config.yaml（可选）控制 `finget fetch latest` 和 `finget scan` 的策略：
  配置哪些数据集参与每日增量 / 查漏补缺。文件不存在时使用内置默认值。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# .env 加载
# ---------------------------------------------------------------------------

# 项目根目录（向上查找 .env 文件）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_env() -> None:
    """加载 .env 文件到环境变量.

    从项目根目录查找 .env，不覆盖已存在的环境变量
    （保证 系统环境变量 > .env 文件 的优先级）。
    """
    env_path = _PROJECT_ROOT / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=False)


# ---------------------------------------------------------------------------
# 配置模型 — 所有默认值都 hardcode 在此
# ---------------------------------------------------------------------------


class TushareConfig(BaseModel):
    """tushare 数据源配置.

    用户唯一需要提供的是 token（从 .env 读）。
    其他参数全部 hardcode，修改需要改源码。
    """

    token: str
    # 镜像站地址列表 — 初始化时自动测速选择最快的
    mirror_urls: list[str] = Field(default_factory=lambda: [
        "https://fast.xiaodefa.cn",
        "https://tt.xiaodefa.cn",
    ])
    # 单一 API 地址（仅当 mirror_urls 为空时使用）
    base_url: str = "https://api.tushare.pro"
    # 接口调用频率限制（次/分钟）
    rate_limit_per_min: int = 400
    # 单次拉取最大行数
    page_size: int = 5000
    # 测速超时秒数
    speed_test_timeout: float = 5.0


class StorageConfig(BaseModel):
    """存储配置 — DuckDB 文件路径默认 data/finget.duckdb."""

    db_path: str = "data/finget.duckdb"
    in_memory: bool = False


class DatasetConfig(BaseModel):
    """单个数据集配置."""

    name: str
    type: str
    api_name: str
    enabled: bool = True
    params: dict[str, Any] = Field(default_factory=dict)
    # 是否支持按 trade_date 单日全市场拉取（adj_factor / daily / daily_basic 是）
    daily_supported: bool = False


class FetcherConfig(BaseModel):
    """数据获取层配置."""

    tushare: TushareConfig | None = None


class UpdateConfig(BaseModel):
    """更新策略配置 — hardcode 默认值."""

    # 全量初始化时回溯多少年
    full_lookback_years: int = 10
    # 增量更新时回溯多少天
    incremental_lookback_days: int = 5
    # 查漏补缺扫描的批次大小
    scan_batch_size: int = 100
    # fetch 不传日期时默认往前倒推的天数
    default_lookback_days: int = 365


class StrategyConfig(BaseModel):
    """策略配置文件 (config.yaml) 的数据模型.

    控制 `finget fetch latest` 和 `finget scan` 命令的行为：
    - latest_datasets: 每日增量更新时处理哪些数据集（按 UpdateMode.DAILY 或 INCREMENTAL）
    - scan_datasets: 查漏补缺时扫描哪些数据集
    """

    latest_datasets: list[str] = Field(default_factory=lambda: [
        "stock_basic", "trade_cal",
        "daily", "adj_factor", "daily_basic", "stk_factor_pro",
    ])
    scan_datasets: list[str] = Field(default_factory=lambda: [
        "daily", "weekly", "adj_factor", "daily_basic",
    ])


# 默认数据集列表（hardcode 在此）
DEFAULT_DATASETS: list[DatasetConfig] = [
    DatasetConfig(name="stock_basic", type="stock_basic", api_name="stock_basic",
                  params={"list_status": "L"}),
    DatasetConfig(name="daily", type="daily", api_name="daily", params={},
                  daily_supported=True),
    DatasetConfig(name="weekly", type="weekly", api_name="weekly", params={}),
    DatasetConfig(name="adj_factor", type="adj_factor", api_name="adj_factor", params={},
                  daily_supported=True),
    DatasetConfig(name="daily_basic", type="daily_basic", api_name="daily_basic", params={},
                  daily_supported=True),
    DatasetConfig(name="trade_cal", type="trade_cal", api_name="trade_cal",
                  params={"exchange": "SSE"}),
    # 卖方研报盈利预测（券商研究报告）
    # 数据从 2010 年开始，每晚 19~22 点更新，单次最大 3000 条
    # 走 FULL 模式（按 ts_code 逐个拉）——tushare 不支持按日全市场查询
    DatasetConfig(name="report_rc", type="report_rc", api_name="report_rc", params={}),
    # 股票技术面因子（MACD/KDJ/RSI/BOLL/EMA/MA 等，含复权版本）
    # 单次最多 10000 条，支持按 trade_date 全市场查询
    DatasetConfig(
        name="stk_factor_pro", type="stk_factor_pro", api_name="stk_factor_pro",
        params={}, daily_supported=True,
    ),
    # 券商月度金股（券商每月荐股）
    # 一般1日~3日内更新当月数据，单次最大 1000 条，按 month(YYYYMM) 拉取
    DatasetConfig(name="broker_recommend", type="broker_recommend",
                  api_name="broker_recommend", params={}),
    # 机构调研记录（上市公司机构调研）
    # 单次最大 100 条，逐标的拉取；content 大文本拆 stk_surv_detail 表
    DatasetConfig(name="stk_surv", type="stk_surv", api_name="stk_surv", params={}),
    # 港美股基础信息（港股 hk_basic + 美股 us_basic 合并写入）
    DatasetConfig(name="hk_us_basic", type="hk_us_basic", api_name="hk_us_basic", params={}),
]


class Config(BaseModel):
    """顶层配置."""

    storage: StorageConfig = Field(default_factory=StorageConfig)
    fetcher: FetcherConfig = Field(default_factory=FetcherConfig)
    update: UpdateConfig = Field(default_factory=UpdateConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    datasets: list[DatasetConfig] = Field(default_factory=lambda: list(DEFAULT_DATASETS))
    log_level: str = "INFO"

    @field_validator("log_level")
    @classmethod
    def validate_level(cls, v: str) -> str:
        v = v.upper()
        if v not in {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(f"Invalid log level: {v}")
        return v


# ---------------------------------------------------------------------------
# 加载器
# ---------------------------------------------------------------------------


def _load_strategy_config() -> StrategyConfig:
    """从 config.yaml 加载策略配置.

    查找路径（按优先级）：
    1. 环境变量 FINGET_CONFIG_FILE 指定的路径
    2. 项目根目录下的 config.yaml
    3. 当前工作目录下的 config.yaml

    文件不存在时返回默认 StrategyConfig。
    """
    search_paths: list[Path] = []

    env_path = os.environ.get("FINGET_CONFIG_FILE")
    if env_path:
        search_paths.append(Path(env_path))

    search_paths.append(_PROJECT_ROOT / "config.yaml")
    search_paths.append(Path.cwd() / "config.yaml")

    for p in search_paths:
        if p.exists():
            try:
                import yaml

                with open(p, encoding="utf-8") as f:
                    raw = yaml.safe_load(f) or {}
                return StrategyConfig(**raw)
            except Exception as e:
                log_warning = os.environ.get("FINGET_LOG_LEVEL", "INFO").upper()
                if log_warning in ("DEBUG", "TRACE"):
                    import logging

                    logging.getLogger("finget").warning(
                        f"Failed to load strategy config from {p}: {e}, using defaults"
                    )
                return StrategyConfig()

    return StrategyConfig()


def _build_config_from_env() -> Config:
    """从环境变量（已加载 .env）构建 Config.

    从外部读取：
    - TUSHARE_TOKEN（必填）
    - TUSHARE_MIRROR_URLS（可选）
    - FINGET_DB_PATH（可选，默认 data/finget.duckdb）
    - FINGET_LOG_LEVEL（可选，默认 INFO）
    - config.yaml（可选，控制 fetch latest/scan 策略）

    其他全部用模型默认值。
    """
    _load_env()

    token = os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN not found. Please set it in .env file or as environment variable.\n"
            "Create a .env file in the project root with:\n"
            "  TUSHARE_TOKEN=your_token_here"
        )

    log_level = os.environ.get("FINGET_LOG_LEVEL", "INFO").upper()
    db_path = os.environ.get("FINGET_DB_PATH", "data/finget.duckdb")
    # 相对路径基于项目根目录解析（而非当前工作目录），避免在子目录跑命令时建错位置
    if not os.path.isabs(db_path):
        db_path = str(_PROJECT_ROOT / db_path)

    # 构建 tushare 配置
    mirror_urls_env = os.environ.get("TUSHARE_MIRROR_URLS")
    if mirror_urls_env:
        mirror_urls = [s.strip() for s in mirror_urls_env.split(",") if s.strip()]
    else:
        mirror_urls = ["https://fast.xiaodefa.cn", "https://tt.xiaodefa.cn"]

    return Config(
        storage=StorageConfig(db_path=db_path),
        fetcher=FetcherConfig(
            tushare=TushareConfig(
                token=token,
                mirror_urls=mirror_urls,
            ),
        ),
        strategy=_load_strategy_config(),
        log_level=log_level,
    )


@lru_cache(maxsize=1)
def get_config() -> Config:
    """获取配置单例.

    从 .env / 环境变量加载，策略配置从 config.yaml 加载。
    """
    return _build_config_from_env()


# 保留 load_config 作为向后兼容的接口
def load_config(path: str | Path | None = None) -> Config:
    """加载配置.

    旧接口：path 参数被忽略（保留仅为向后兼容）。
    新逻辑：从 .env / 环境变量读取配置，从 config.yaml 读取策略。
    """
    return _build_config_from_env()
