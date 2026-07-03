"""从同花顺自选股同步 watchlist.

独立脚本，不属于 finget 主程序。调 extra/ths-favorite 库登录同花顺，
拉取自选股分组，转换为 tushare 兼容的 ts_code，写入 watchlist.yaml。

运行方式（在 ths-favorite 目录，复用其 venv）:
    cd extra/ths-favorite
    uv run python ../../scripts/sync_watchlist.py

或在 finget 根目录（需 ths-favorite 依赖已装到 finget venv）:
    uv run python scripts/sync_watchlist.py

前提:
    1. .env 中配置了 THS_USERNAME / THS_PASSWORD
    2. extra/ths-favorite 依赖已安装（cd extra/ths-favorite && uv sync）

ts_code 兼容性:
    ths-favorite 的 market 缩写/数字码 → tushare ts_code 后缀:
        SH/ST/SHETF → .SH
        SZ/SZETF    → .SZ
        BJ          → .BJ
        CYB         → 按代码前缀 300/301 → .SZ
        KC          → 按代码前缀 688/787 → .SH
        177（港股） → .HK（HK0189 → 00189.HK）
        185/169/217（美股）→ .US（字母代码如 SITM.US）
        指数（120/ZS）/期货/期权/债券/新三板 → 过滤
"""

from __future__ import annotations

import os
import sys
from collections import Counter, defaultdict
from pathlib import Path

# 把 extra/ths-favorite 加入 sys.path（它是顶层模块，非包）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_THS_DIR = _PROJECT_ROOT / "extra" / "ths-favorite"
sys.path.insert(0, str(_THS_DIR))


def _load_env(path: Path) -> dict[str, str]:
    """简易 .env 解析（不依赖 python-dotenv）。"""
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().strip("'\"")
    return env


# ---------------------------------------------------------------------------
# ts_code 转换
# ---------------------------------------------------------------------------

# 直接映射的 market（market 缩写/数字码 → tushare 后缀）
_DIRECT_MAP = {
    "SH": ".SH",
    "ST": ".SH",  # 上交所 ST，本质 SH
    "SZ": ".SZ",
    "BJ": ".BJ",
    "SHETF": ".SH",  # 上交所 ETF
    "SZETF": ".SZ",  # 深交所 ETF
    # 港股（ths 数字码 177）
    "177": ".HK",
    # 美股（ths 数字码 185/169/217，代码是字母如 SITM/WOLF）
    "185": ".US",
    "169": ".US",
    "217": ".US",
}

# 需要按代码前缀判断的 market
# CYB（创业板）: 300/301 开头 → SZ
# KC（科创板）: 688/787 开头 → SH
_PREFIX_MAP = {
    "CYB": {("300", "301"): ".SZ"},
    "KC": {("688", "787"): ".SH"},
}


def _normalize_hk_code(code: str) -> str:
    """把同花顺港股代码转为 tushare 格式.

    同花顺: HK0189 / HK00700
    tushare: 00189 / 00700（5 位数字）

    去掉 HK 前缀，补零到 5 位。
    """
    digits = code.lstrip("HK").lstrip("hk")
    return digits.zfill(5)


def to_ts_code(code: str, market: str | None) -> str | None:
    """把 ths-favorite 的 code+market 转成 tushare ts_code.

    Returns:
        ts_code（如 "002831.SZ"/"00700.HK"/"AAPL.US"），需过滤时返回 None.
    """
    if not market:
        return None
    market = market.upper()

    # 直接映射
    if market in _DIRECT_MAP:
        suffix = _DIRECT_MAP[market]
        # 港股代码需要特殊处理（HK0189 → 00189）
        if suffix == ".HK":
            return f"{_normalize_hk_code(code)}{suffix}"
        return f"{code}{suffix}"

    # 按前缀映射
    if market in _PREFIX_MAP:
        for prefixes, suffix in _PREFIX_MAP[market].items():
            if any(code.startswith(p) for p in prefixes):
                return f"{code}{suffix}"
        # 前缀不匹配，无法转换
        return None

    # 过滤（指数 120/ZS、期货/期权/债券/新三板等）
    return None


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main() -> None:
    env = _load_env(_PROJECT_ROOT / ".env")
    # 系统环境变量优先于 .env
    username = os.environ.get("THS_USERNAME") or env.get("THS_USERNAME")
    password = os.environ.get("THS_PASSWORD") or env.get("THS_PASSWORD")
    if not username or not password:
        print("错误: 未配置 THS_USERNAME / THS_PASSWORD，请在 .env 中设置")
        sys.exit(1)

    # 抑制 ths-favorite 的 loguru 日志（只保留 WARNING 以上）
    from loguru import logger as ths_logger
    ths_logger.remove()
    ths_logger.add(sys.stderr, level="WARNING")

    from service import PortfolioManager

    print(f"▶ 登录同花顺 ({username}) ...")
    with PortfolioManager(username=username, password=password) as portfolio:
        groups = portfolio.get_all_groups()
        self_group = portfolio.get_self_stocks()
    print(f"▶ 获取自选股数据 ...")
    print(f"  分组: {len(groups)} 个, 自选股: {len(self_group.items)} 只")

    # 收集每只股票的 tags（ts_code → set of tag）
    watchlist: dict[str, set[str]] = defaultdict(set)
    skipped: list[tuple[str, str | None]] = []  # (code, market) 被过滤的

    # 各分组的股票
    for group_key, group in groups.items():
        tag = group.name  # 用分组名作为 tag
        for item in group.items:
            ts_code = to_ts_code(item.code, item.market)
            if ts_code is None:
                skipped.append((item.code, item.market))
                continue
            watchlist[ts_code].add(tag)

    # 自选股（特殊 tag "自选"）
    for item in self_group.items:
        ts_code = to_ts_code(item.code, item.market)
        if ts_code is None:
            skipped.append((item.code, item.market))
            continue
        watchlist[ts_code].add("自选")

    # 写入 watchlist.yaml（手动生成 YAML，不依赖 pyyaml）
    output_path = _PROJECT_ROOT / "watchlist.yaml"
    lines = [
        "# watchlist — 由 scripts/sync_watchlist.py 从同花顺自选股同步生成",
        "# 格式: ts_code: tags（列表）",
        "# 手动编辑后会被重新 sync 覆盖，如需保留手动修改请备份",
        "",
    ]
    for ts_code in sorted(watchlist):
        tags = sorted(watchlist[ts_code])
        tags_str = ", ".join(tags)
        lines.append(f"{ts_code}:")
        lines.append(f"  tags: [{tags_str}]")
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 按市场后缀统计
    suffix_counts = Counter(ts_code.rsplit(".", 1)[-1] for ts_code in watchlist)
    suffix_names = {"SH": "沪市", "SZ": "深市", "BJ": "北交所", "HK": "港股", "US": "美股"}

    print(f"\n{'=' * 50}")
    print(f"✓ 同步完成 → {output_path}")
    print(f"{'=' * 50}")
    print(f"  保留标的: {len(watchlist)} 只")
    for suffix in ["SH", "SZ", "BJ", "HK", "US"]:
        if suffix in suffix_counts:
            print(f"    {suffix_names[suffix]:6} (.{suffix}): {suffix_counts[suffix]} 只")
    if skipped:
        skipped_counts = Counter(m for _, m in skipped)
        print(f"  过滤标的: {len(skipped)} 条（指数/期货/期权等）")
        for m, c in skipped_counts.most_common():
            print(f"    {m}: {c}")


if __name__ == "__main__":
    main()
