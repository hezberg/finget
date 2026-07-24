"""配置层测试 — 适配极简 config 设计.

无 YAML 文件。所有配置从代码默认值 + .env 推导。
唯一从环境读的是 TUSHARE_TOKEN。
"""

import pytest

from finget.config import (
    Config,
    DEFAULT_DATASETS,
    StorageConfig,
    TushareConfig,
    get_config,
    load_config,
)
from finget.config import StrategyConfig, _load_strategy_config


class TestConfigDefaults:
    """模型默认值 — 全部 hardcode."""

    def test_default_storage(self):
        cfg = Config()
        assert cfg.storage.db_path == "data/finget.duckdb"
        assert cfg.storage.in_memory is False

    def test_default_log_level(self):
        cfg = Config()
        assert cfg.log_level == "INFO"

    def test_default_datasets_count(self):
        """默认应有 11 个数据集."""
        assert len(DEFAULT_DATASETS) == 12
        names = {d.name for d in DEFAULT_DATASETS}
        assert names == {"stock_basic", "daily", "weekly", "adj_factor", "daily_basic", "trade_cal", "report_rc", "stk_factor_pro", "broker_recommend", "stk_surv", "hk_us_basic", "ths_index"}

    def test_tushare_defaults(self):
        tc = TushareConfig(token="test")
        assert tc.token == "test"
        assert tc.rate_limit_per_min == 400
        assert tc.page_size == 5000
        # mirror_urls 默认值应包含 fast.xiaodefa.cn 和 tt.xiaodefa.cn
        assert "https://fast.xiaodefa.cn" in tc.mirror_urls
        assert "https://tt.xiaodefa.cn" in tc.mirror_urls

    def test_invalid_log_level(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            Config(log_level="INVALID")


class TestLoadConfig:
    """load_config() 测试 — 从 .env 加载."""

    def test_load_config_requires_token(self):
        """无 TUSHARE_TOKEN 时应抛出友好错误."""
        # conftest 已清空环境变量
        with pytest.raises(ValueError) as exc_info:
            load_config()
        assert "TUSHARE_TOKEN not found" in str(exc_info.value)
        # 应提示创建 .env
        assert ".env" in str(exc_info.value)

    def test_load_config_with_token(self, monkeypatch):
        """设置 TUSHARE_TOKEN 后应正常加载."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test_token_123")
        cfg = load_config()
        assert cfg.fetcher.tushare is not None
        assert cfg.fetcher.tushare.token == "test_token_123"
        # 其他字段用默认值
        assert cfg.fetcher.tushare.rate_limit_per_min == 400
        # db_path 相对路径会被解析成基于项目根的绝对路径
        assert cfg.storage.db_path.endswith("data/finget.duckdb")

    def test_load_config_mirror_urls_from_env(self, monkeypatch):
        """TUSHARE_MIRROR_URLS 覆盖默认镜像站."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("TUSHARE_MIRROR_URLS", "https://a.cn,https://b.cn")
        cfg = load_config()
        assert cfg.fetcher.tushare.mirror_urls == ["https://a.cn", "https://b.cn"]

    def test_load_config_mirror_urls_trims_whitespace(self, monkeypatch):
        """TUSHARE_MIRROR_URLS 自动去除空格."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("TUSHARE_MIRROR_URLS", " https://a.cn , https://b.cn ")
        cfg = load_config()
        assert cfg.fetcher.tushare.mirror_urls == ["https://a.cn", "https://b.cn"]

    def test_load_config_log_level_from_env(self, monkeypatch):
        """FINGET_LOG_LEVEL 覆盖默认日志级别."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        monkeypatch.setenv("FINGET_LOG_LEVEL", "DEBUG")
        cfg = load_config()
        assert cfg.log_level == "DEBUG"

    def test_load_config_datasets_default(self, monkeypatch):
        """不指定 datasets 时用默认 11 个数据集."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        cfg = load_config()
        assert len(cfg.datasets) == 12
        assert {d.name for d in cfg.datasets} == {
            "stock_basic", "daily", "weekly", "adj_factor", "daily_basic", "trade_cal", "report_rc", "stk_factor_pro", "broker_recommend", "stk_surv", "hk_us_basic", "ths_index"
        }

    def test_load_config_old_yaml_path_ignored(self, monkeypatch, tmp_path):
        """load_config 接受 path 参数但忽略（向后兼容）."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        fake_yaml = tmp_path / "nonexistent.yaml"
        # 不应报错（path 被忽略）
        cfg = load_config(fake_yaml)
        assert cfg.fetcher.tushare.token == "test"


class TestGetConfig:
    """get_config() 单例测试."""

    def test_get_config_singleton(self, monkeypatch):
        """多次调用返回同一对象（lru_cache 缓存）."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        get_config.cache_clear()
        cfg1 = get_config()
        cfg2 = get_config()
        assert cfg1 is cfg2

    def test_get_config_no_args(self, monkeypatch):
        """get_config() 无参数，直接从 .env 读."""
        monkeypatch.setenv("TUSHARE_TOKEN", "test")
        get_config.cache_clear()
        cfg = get_config()
        assert cfg.fetcher.tushare.token == "test"


class TestStrategyConfigLoading:
    """策略配置文件 (config.yaml) 加载测试."""

    def test_default_strategy_config(self):
        """无配置文件时使用内置默认值."""
        cfg = StrategyConfig()
        assert cfg.latest_datasets == ["daily", "adj_factor", "daily_basic", "stk_factor_pro"]
        assert cfg.scan_datasets == ["daily", "weekly", "adj_factor", "daily_basic"]

    def test_load_strategy_config_from_env(self, monkeypatch, tmp_path):
        """通过 FINGET_CONFIG_FILE 环境变量加载自定义 config.yaml."""
        yaml_content = (
            "latest_datasets:\n"
            "  - daily\n"
            "  - weekly\n"
            "scan_datasets:\n"
            "  - daily\n"
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        monkeypatch.setenv("FINGET_CONFIG_FILE", str(cfg_file))
        cfg = _load_strategy_config()
        assert cfg.latest_datasets == ["daily", "weekly"]
        assert cfg.scan_datasets == ["daily"]

    def test_load_strategy_config_partial(self, monkeypatch, tmp_path):
        """config.yaml 只配置部分字段时，另一字段用默认值."""
        yaml_content = "scan_datasets:\n  - adj_factor\n"
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml_content, encoding="utf-8")

        monkeypatch.setenv("FINGET_CONFIG_FILE", str(cfg_file))
        cfg = _load_strategy_config()
        assert cfg.scan_datasets == ["adj_factor"]
        # latest_datasets 未配置 → 用默认值
        assert cfg.latest_datasets == ["daily", "adj_factor", "daily_basic", "stk_factor_pro"]

    def test_load_strategy_config_file_not_found(self, monkeypatch, tmp_path):
        """配置文件不存在时返回默认 StrategyConfig."""
        # 指向不存在的文件 + 项目根/cwd 都指向 tmp_path（无 config.yaml）
        import finget.config as _cfg
        monkeypatch.setattr(_cfg, "_PROJECT_ROOT", tmp_path)
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("FINGET_CONFIG_FILE", raising=False)
        cfg = _load_strategy_config()
        assert cfg.latest_datasets == ["daily", "adj_factor", "daily_basic", "stk_factor_pro"]
        assert cfg.scan_datasets == ["daily", "weekly", "adj_factor", "daily_basic"]

    def test_load_strategy_config_invalid_yaml(self, monkeypatch, tmp_path):
        """config.yaml 内容损坏时回退到默认值（不抛异常）."""
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("latest_datasets: [unclosed bracket", encoding="utf-8")

        monkeypatch.setenv("FINGET_CONFIG_FILE", str(cfg_file))
        cfg = _load_strategy_config()
        # 损坏的 YAML 解析失败 → 回退默认
        assert cfg.latest_datasets == ["daily", "adj_factor", "daily_basic", "stk_factor_pro"]
