"""REST API 路由 — 所有数据接口."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Query

if TYPE_CHECKING:
    import pandas as pd

from finget.server.store_reader import get_store_reader

router = APIRouter()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------


def _norm_date(d: str) -> str:
    """归一化日期为 YYYY-MM-DD（DuckDB DATE 类型要求此格式）."""
    d = d.replace("-", "")
    if len(d) == 8:
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def _safe_float(v: Any) -> float | None:
    """安全转 float，NaN/None → None."""
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN != NaN
    except (ValueError, TypeError):
        return None


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """DataFrame → JSON 可序列化的 dict 列表."""
    if df.empty:
        return []
    # replace NaN/NaT with None for valid JSON
    df = df.where(df.notna(), None)
    return json.loads(df.to_json(orient="records", date_format="iso", force_ascii=False))


def _query(sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    """执行 SQL 查询."""
    reader = get_store_reader()
    try:
        return reader.query(sql, params)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {e}") from e


# ---------------------------------------------------------------------------
# 仪表盘 / 统计
# ---------------------------------------------------------------------------


@router.get("/overview")
async def overview():
    """数据总览 — 各表行数、日期范围、标的数."""
    reader = get_store_reader()
    tables = reader.list_tables()

    # 排除 detail 表（附属表）
    skip_tables = {"stk_surv_detail"}

    result: list[dict[str, Any]] = []
    for t in tables:
        if t in skip_tables:
            continue
        try:
            count = reader.query(f"SELECT COUNT(*) AS cnt FROM {t}").iloc[0, 0]
        except Exception:
            count = 0

        entry: dict[str, Any] = {
            "table": t,
            "rows": int(count),
            "min_date": None,
            "max_date": None,
            "stock_count": None,
        }

        # 尝试获取日期范围
        date_col = None
        if t in ("daily", "weekly", "adj_factor", "daily_basic", "stk_factor_pro"):
            date_col = "trade_date"
        elif t == "trade_cal":
            date_col = "cal_date"
        elif t == "report_rc":
            date_col = "report_date"
        elif t == "stk_surv":
            date_col = "surv_date"
        elif t == "broker_recommend":
            # 无标准日期列，用 month 字符串
            try:
                df = reader.query(
                    f"SELECT MIN(month) AS mn, MAX(month) AS mx FROM {t}"
                )
                if not df.empty:
                    entry["min_date"] = str(df.iloc[0, 0]) if df.iloc[0, 0] else None
                    entry["max_date"] = str(df.iloc[0, 1]) if df.iloc[0, 1] else None
            except Exception:
                pass

        if date_col:
            try:
                df = reader.query(
                    f"SELECT MIN({date_col}) AS mn, MAX({date_col}) AS mx FROM {t}"
                )
                if not df.empty:
                    entry["min_date"] = (
                        str(df.iloc[0, 0])[:10] if df.iloc[0, 0] else None
                    )
                    entry["max_date"] = (
                        str(df.iloc[0, 1])[:10] if df.iloc[0, 1] else None
                    )
            except Exception:
                pass

        # 标的数
        if t in ("daily", "weekly", "adj_factor", "daily_basic", "stk_factor_pro",
                 "report_rc", "stk_surv", "broker_recommend"):
            try:
                df = reader.query(
                    f"SELECT COUNT(DISTINCT ts_code) AS cnt FROM {t}"
                )
                entry["stock_count"] = int(df.iloc[0, 0])
            except Exception:
                pass
        elif t == "stock_basic" or t == "hk_us_basic":
            entry["stock_count"] = int(count)

        result.append(entry)

    return result


@router.get("/industry_dist")
async def industry_dist():
    """行业分布."""
    df = _query(
        "SELECT industry, COUNT(*) AS cnt FROM stock_basic "
        "WHERE industry IS NOT NULL AND industry != '' "
        "GROUP BY industry ORDER BY cnt DESC"
    )
    return _df_to_records(df)


@router.get("/stocks/search")
async def search_stocks(q: str = Query("", description="搜索关键词")):
    """股票搜索 — 按代码或名称模糊匹配."""
    if not q:
        return []
    df = _query(
        "SELECT ts_code, name, fullname, industry, area, market, exchange, list_date "
        "FROM stock_basic "
        "WHERE ts_code LIKE ? OR name LIKE ? OR fullname LIKE ? OR cnspell LIKE ? "
        "ORDER BY ts_code LIMIT 50",
        [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"],
    )
    if df.empty:
        # 也搜港美股
        df = _query(
            "SELECT ts_code, name, enname FROM hk_us_basic "
            "WHERE ts_code LIKE ? OR name LIKE ? OR enname LIKE ? "
            "ORDER BY ts_code LIMIT 50",
            [f"%{q}%", f"%{q}%", f"%{q}%"],
        )
    return _df_to_records(df)


@router.get("/stocks/{ts_code}")
async def stock_info(ts_code: str):
    """股票基础信息 + 最新指标."""
    # stock_basic
    df = _query(
        "SELECT * FROM stock_basic WHERE ts_code = ?", [ts_code]
    )
    if df.empty:
        # 试港美股
        df = _query(
            "SELECT * FROM hk_us_basic WHERE ts_code = ?", [ts_code]
        )
        if df.empty:
            raise HTTPException(status_code=404, detail=f"未找到股票: {ts_code}")
        return {"basic": _df_to_records(df)[0], "latest_metrics": {}}

    basic = _df_to_records(df)[0]

    # 最新 daily_basic 指标
    latest_metrics: dict[str, Any] = {}
    try:
        df2 = _query(
            "SELECT * FROM daily_basic WHERE ts_code = ? ORDER BY trade_date DESC LIMIT 1",
            [ts_code],
        )
        if not df2.empty:
            latest_metrics = _df_to_records(df2)[0]
    except Exception:
        pass

    return {"basic": basic, "latest_metrics": latest_metrics}


# ---------------------------------------------------------------------------
# K线 & 指标
# ---------------------------------------------------------------------------


@router.get("/kline/{ts_code}")
async def kline(
    ts_code: str,
    table: str = Query("daily", description="daily 或 weekly"),
    start: str | None = Query(None, description="起始日期 YYYY-MM-DD 或 YYYYMMDD"),
    end: str | None = Query(None, description="结束日期"),
    adj: str | None = Query("qfq", description="复权: qfq=前复权, hfq=后复权, None=不复权"),
    limit: int = Query(500, description="最大返回行数"),
):
    """获取 K 线数据 — 返回 TradingView Lightweight Charts 兼容格式.

    默认前复权(qfq)，与券商 app 显示一致。
    """
    if table not in ("daily", "weekly"):
        raise HTTPException(status_code=400, detail="table 参数仅支持 daily 或 weekly")

    conditions = ["d.ts_code = ?"]
    params: list[Any] = [ts_code]

    if start:
        conditions.append("d.trade_date >= ?")
        params.append(_norm_date(start))
    if end:
        conditions.append("d.trade_date <= ?")
        params.append(_norm_date(end))

    where = f" WHERE {' AND '.join(conditions)}"

    # 复权处理：LEFT JOIN adj_factor，前复权/后复权均归一化到最新日
    if adj == "qfq":
        price_expr = (
            "d.open * COALESCE(a.adj_factor, 1) / latest.f AS open, "
            "d.high * COALESCE(a.adj_factor, 1) / latest.f AS high, "
            "d.low * COALESCE(a.adj_factor, 1) / latest.f AS low, "
            "d.close * COALESCE(a.adj_factor, 1) / latest.f AS close"
        )
        join = (
            " LEFT JOIN adj_factor a ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date"
            " CROSS JOIN (SELECT COALESCE(MAX(adj_factor), 1) AS f FROM adj_factor"
            "   WHERE ts_code = ?) latest"
        )
        # 把 ts_code 加到 params 最前面（CROSS JOIN 子查询用）
        params.insert(0, ts_code)
    elif adj == "hfq":
        price_expr = (
            "d.open / COALESCE(NULLIF(a.adj_factor, 0), 1) * latest.f AS open, "
            "d.high / COALESCE(NULLIF(a.adj_factor, 0), 1) * latest.f AS high, "
            "d.low / COALESCE(NULLIF(a.adj_factor, 0), 1) * latest.f AS low, "
            "d.close / COALESCE(NULLIF(a.adj_factor, 0), 1) * latest.f AS close"
        )
        join = (
            " LEFT JOIN adj_factor a ON d.ts_code = a.ts_code AND d.trade_date = a.trade_date"
            " CROSS JOIN (SELECT COALESCE(MAX(adj_factor), 1) AS f FROM adj_factor"
            "   WHERE ts_code = ?) latest"
        )
        params.insert(0, ts_code)
    else:
        price_expr = "d.open, d.high, d.low, d.close"
        join = ""

    sql = (
        f"SELECT d.trade_date, {price_expr}, d.vol, d.amount "
        f"FROM {table} d{join}{where} "
        f"ORDER BY d.trade_date ASC "
        f"LIMIT {limit}"
    )
    df = _query(sql, params)

    records = []
    for _, row in df.iterrows():
        d = str(row["trade_date"])[:10] if row["trade_date"] else None
        records.append({
            "time": d,
            "open": _safe_float(row.get("open")),
            "high": _safe_float(row.get("high")),
            "low": _safe_float(row.get("low")),
            "close": _safe_float(row.get("close")),
            "volume": _safe_float(row.get("vol")),
            "amount": _safe_float(row.get("amount")),
        })
    return records


@router.get("/indicators/{ts_code}")
async def indicators(
    ts_code: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(500),
):
    """获取技术指标（MACD / KDJ / RSI / BOLL / MA）."""
    conditions = ["ts_code = ?"]
    params: list[Any] = [ts_code]

    if start:
        conditions.append("trade_date >= ?")
        params.append(_norm_date(start))
    if end:
        conditions.append("trade_date <= ?")
        params.append(_norm_date(end))

    where = f" WHERE {' AND '.join(conditions)}"

    # 选取 qfq（前复权）版本的指标列
    cols = [
        "trade_date",
        "macd_dif_qfq", "macd_dea_qfq", "macd_qfq",
        "kdj_k_qfq", "kdj_d_qfq", "kdj_qfq",
        "rsi_qfq_6", "rsi_qfq_12", "rsi_qfq_24",
        "boll_upper_qfq", "boll_mid_qfq", "boll_lower_qfq",
        "ma_qfq_5", "ma_qfq_10", "ma_qfq_20", "ma_qfq_60",
    ]
    col_str = ", ".join(cols)
    sql = (
        f"SELECT {col_str} FROM stk_factor_pro{where} "
        f"ORDER BY trade_date ASC LIMIT {limit}"
    )
    try:
        df = _query(sql, params)
    except Exception:
        return []  # stk_factor_pro 表可能不存在

    return _df_to_records(df)


@router.get("/daily_basic/{ts_code}")
async def daily_basic_metrics(
    ts_code: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    limit: int = Query(500),
):
    """获取每日基本面指标（PE / PB / PS / 换手率 / 市值等）."""
    conditions = ["ts_code = ?"]
    params: list[Any] = [ts_code]

    if start:
        conditions.append("trade_date >= ?")
        params.append(_norm_date(start))
    if end:
        conditions.append("trade_date <= ?")
        params.append(_norm_date(end))

    where = f" WHERE {' AND '.join(conditions)}"
    sql = (
        f"SELECT trade_date, close, pe, pe_ttm, pb, ps, ps_ttm, "
        f"turnover_rate, volume_ratio, total_mv, circ_mv, dv_ratio "
        f"FROM daily_basic{where} ORDER BY trade_date ASC LIMIT {limit}"
    )
    try:
        df = _query(sql, params)
    except Exception:
        return []
    return _df_to_records(df)


# ---------------------------------------------------------------------------
# 卖方研报
# ---------------------------------------------------------------------------


@router.get("/research/{ts_code}")
async def research_reports(
    ts_code: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    org: str | None = Query(None),
    limit: int = Query(200),
):
    """获取卖方研报列表."""
    conditions = ["ts_code = ?"]
    params: list[Any] = [ts_code]

    if start:
        conditions.append("report_date >= ?")
        params.append(_norm_date(start))
    if end:
        conditions.append("report_date <= ?")
        params.append(_norm_date(end))
    if org:
        conditions.append("org_name LIKE ?")
        params.append(f"%{org}%")

    where = f" WHERE {' AND '.join(conditions)}"
    sql = (
        f"SELECT * FROM report_rc{where} "
        f"ORDER BY report_date DESC LIMIT {limit}"
    )
    try:
        df = _query(sql, params)
    except Exception:
        return []
    return _df_to_records(df)


@router.get("/research/{ts_code}/eps_trend")
async def eps_trend(
    ts_code: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
):
    """EPS 预测趋势 — 各机构按季度预测."""
    conditions = ["ts_code = ?", "eps IS NOT NULL"]
    params: list[Any] = [ts_code]

    if start:
        conditions.append("report_date >= ?")
        params.append(_norm_date(start))
    if end:
        conditions.append("report_date <= ?")
        params.append(_norm_date(end))

    where = f" WHERE {' AND '.join(conditions)}"
    sql = (
        f"SELECT report_date, org_name, quarter, eps, pe, rating, max_price "
        f"FROM report_rc{where} "
        f"ORDER BY report_date ASC"
    )
    try:
        df = _query(sql, params)
    except Exception:
        return []
    return _df_to_records(df)


@router.get("/research/orgs")
async def research_orgs():
    """研报机构列表（Top 50）."""
    try:
        df = _query(
            "SELECT org_name, COUNT(*) AS cnt FROM report_rc "
            "WHERE org_name IS NOT NULL "
            "GROUP BY org_name ORDER BY cnt DESC LIMIT 50"
        )
    except Exception:
        return []
    return _df_to_records(df)


# ---------------------------------------------------------------------------
# 机构调研
# ---------------------------------------------------------------------------


@router.get("/survey/{ts_code}")
async def survey_records(
    ts_code: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    with_content: bool = Query(False),
    limit: int = Query(100),
):
    """获取机构调研记录."""
    conditions = ["s.ts_code = ?"]
    params: list[Any] = [ts_code]

    if start:
        conditions.append("s.surv_date >= ?")
        params.append(_norm_date(start))
    if end:
        conditions.append("s.surv_date <= ?")
        params.append(_norm_date(end))

    where = f" WHERE {' AND '.join(conditions)}"

    if with_content:
        sql = (
            f"SELECT s.*, d.content FROM stk_surv s "
            f"LEFT JOIN stk_surv_detail d "
            f"ON s.ts_code = d.ts_code AND s.surv_date = d.surv_date "
            f"AND s.rece_org = d.rece_org"
            f"{where} ORDER BY s.surv_date DESC LIMIT {limit}"
        )
    else:
        sql = (
            f"SELECT * FROM stk_surv s{where} "
            f"ORDER BY surv_date DESC LIMIT {limit}"
        )
    try:
        df = _query(sql, params)
    except Exception:
        return []
    return _df_to_records(df)


# ---------------------------------------------------------------------------
# 券商金股
# ---------------------------------------------------------------------------


@router.get("/broker_recommend")
async def broker_recommend(
    month: str | None = Query(None, description="月份 YYYYMM"),
    broker: str | None = Query(None),
    ts_code: str | None = Query(None),
    limit: int = Query(500),
):
    """获取券商月度金股."""
    conditions: list[str] = []
    params: list[Any] = []

    if month:
        conditions.append("month = ?")
        params.append(month)
    if broker:
        conditions.append("broker LIKE ?")
        params.append(f"%{broker}%")
    if ts_code:
        conditions.append("ts_code = ?")
        params.append(ts_code)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        f"SELECT * FROM broker_recommend{where} "
        f"ORDER BY month DESC, broker, ts_code LIMIT {limit}"
    )
    try:
        df = _query(sql, params)
    except Exception:
        return []
    return _df_to_records(df)


@router.get("/broker_recommend/heatmap")
async def broker_heatmap(
    start_month: str | None = Query(None, description="起始月份 YYYYMM"),
    end_month: str | None = Query(None, description="结束月份 YYYYMM"),
):
    """券商金股热力图数据 — 券商 × 月份 × 推荐次数."""
    conditions: list[str] = []
    params: list[Any] = []

    if start_month:
        conditions.append("month >= ?")
        params.append(start_month)
    if end_month:
        conditions.append("month <= ?")
        params.append(end_month)

    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        f"SELECT broker, month, COUNT(*) AS cnt FROM broker_recommend"
        f"{where} GROUP BY broker, month ORDER BY month, broker"
    )
    try:
        df = _query(sql, params)
    except Exception:
        return []
    return _df_to_records(df)


@router.get("/broker_recommend/months")
async def broker_months():
    """获取金股数据的所有月份列表."""
    try:
        df = _query(
            "SELECT DISTINCT month FROM broker_recommend ORDER BY month DESC"
        )
    except Exception:
        return []
    return [str(r["month"]) for _, r in df.iterrows()]


@router.get("/broker_recommend/brokers")
async def broker_list():
    """获取有金股数据的券商列表."""
    try:
        df = _query(
            "SELECT broker, COUNT(*) AS cnt FROM broker_recommend "
            "GROUP BY broker ORDER BY cnt DESC"
        )
    except Exception:
        return []
    return _df_to_records(df)


@router.get("/broker_recommend/top_stocks")
async def broker_top_stocks(
    month: str = Query(..., description="月份 YYYYMM"),
    limit: int = Query(15, description="返回前 N 只股票"),
):
    """本月热门金股 — 按推荐次数排名，附带当月涨跌幅."""
    top = _query(
        "SELECT ts_code, name, COUNT(*) AS rec_cnt, "
        "COUNT(DISTINCT broker) AS broker_cnt "
        "FROM broker_recommend WHERE month = ? "
        "GROUP BY ts_code, name ORDER BY rec_cnt DESC LIMIT ?",
        [month, limit],
    )
    if top.empty:
        return []

    records = _df_to_records(top)
    month_start = f"{month[:4]}-{month[4:6]}-01"
    y, m = int(month[:4]), int(month[4:6])
    next_start = f"{y}-{m + 1:02d}-01" if m < 12 else f"{y + 1}-01-01"

    for r in records:
        code = r["ts_code"]
        try:
            first_df = _query(
                "SELECT close FROM daily WHERE ts_code = ? AND trade_date >= ? "
                "ORDER BY trade_date ASC LIMIT 1",
                [code, month_start],
            )
            last_df = _query(
                "SELECT close FROM daily WHERE ts_code = ? AND trade_date < ? "
                "ORDER BY trade_date DESC LIMIT 1",
                [code, next_start],
            )
            if not first_df.empty and not last_df.empty:
                fc, lc = float(first_df.iloc[0, 0]), float(last_df.iloc[0, 0])
                r["month_return"] = round((lc - fc) / fc * 100, 2)
                r["first_close"] = round(fc, 2)
                r["last_close"] = round(lc, 2)
            else:
                r["month_return"] = None
        except Exception:
            r["month_return"] = None
    return records


@router.get("/broker_recommend/stock_performance")
async def broker_stock_performance(
    ts_code: str = Query(..., description="股票代码"),
    month: str = Query(..., description="推荐月份 YYYYMM"),
):
    """某只股票在被推荐月份及之后的表现."""
    month_start = f"{month[:4]}-{month[4:6]}-01"
    y, m = int(month[:4]), int(month[4:6])
    m1_start = f"{y}-{m + 1:02d}-01" if m < 12 else f"{y + 1}-01-01"
    m3_start = f"{y}-{m + 3:02d}-01" if m < 10 else f"{y + 1}-{m - 9:02d}-01"

    try:
        first = _query(
            "SELECT trade_date, close FROM daily WHERE ts_code = ? AND trade_date >= ? "
            "ORDER BY trade_date ASC LIMIT 1",
            [ts_code, month_start],
        )
        if first.empty:
            return {"error": "无交易数据"}

        fc = float(first.iloc[0, 1])
        fd = str(first.iloc[0, 0])[:10]

        last = _query(
            "SELECT close FROM daily WHERE ts_code = ? AND trade_date < ? "
            "ORDER BY trade_date DESC LIMIT 1",
            [ts_code, m1_start],
        )
        mr = round((float(last.iloc[0, 0]) - fc) / fc * 100, 2) if not last.empty else None

        m3 = _query(
            "SELECT close FROM daily WHERE ts_code = ? AND trade_date < ? "
            "ORDER BY trade_date DESC LIMIT 1",
            [ts_code, m3_start],
        )
        m3r = round((float(m3.iloc[0, 0]) - fc) / fc * 100, 2) if not m3.empty else None

        brokers = _query(
            "SELECT DISTINCT broker FROM broker_recommend WHERE ts_code = ? AND month = ?",
            [ts_code, month],
        )

        return {
            "ts_code": ts_code, "month": month, "first_date": fd,
            "first_close": round(fc, 2), "month_return": mr, "m3_return": m3r,
            "brokers": [str(b) for b in brokers["broker"].tolist()] if not brokers.empty else [],
        }
    except Exception as e:
        return {"error": str(e)}


@router.get("/broker_recommend/broker_rank")
async def broker_rank(
    month: str = Query(..., description="月份 YYYYMM"),
    limit: int = Query(20),
):
    """券商推荐排名 — 按推荐次数 + 平均命中率."""
    brokers = _query(
        "SELECT broker, COUNT(*) AS rec_cnt, COUNT(DISTINCT ts_code) AS stock_cnt "
        "FROM broker_recommend WHERE month = ? "
        "GROUP BY broker ORDER BY rec_cnt DESC LIMIT ?",
        [month, limit],
    )
    if brokers.empty:
        return []

    records = _df_to_records(brokers)
    month_start = f"{month[:4]}-{month[4:6]}-01"
    y, m = int(month[:4]), int(month[4:6])
    next_start = f"{y}-{m + 1:02d}-01" if m < 12 else f"{y + 1}-01-01"

    for r in records:
        bn = r["broker"]
        try:
            perf = _query(
                "SELECT AVG((l.close - f.close) / NULLIF(f.close, 0) * 100) FROM ("
                "  SELECT DISTINCT br.ts_code FROM broker_recommend br "
                "  WHERE br.broker = ? AND br.month = ?"
                ") rec "
                "JOIN (SELECT ts_code, close, trade_date FROM daily "
                "  WHERE (ts_code, trade_date) IN ("
                "    SELECT ts_code, MIN(trade_date) FROM daily "
                "    WHERE trade_date >= ? GROUP BY ts_code"
                "  )"
                ") f ON rec.ts_code = f.ts_code "
                "JOIN (SELECT ts_code, close FROM daily "
                "  WHERE (ts_code, trade_date) IN ("
                "    SELECT ts_code, MAX(trade_date) FROM daily "
                "    WHERE trade_date < ? GROUP BY ts_code"
                "  )"
                ") l ON rec.ts_code = l.ts_code",
                [bn, month, month_start, next_start],
            )
            if not perf.empty and perf.iloc[0, 0] is not None:
                r["avg_return"] = round(float(perf.iloc[0, 0]), 2)
            else:
                r["avg_return"] = None
        except Exception:
            r["avg_return"] = None
    return records


# ---------------------------------------------------------------------------
# 同花顺概念/行业
# ---------------------------------------------------------------------------


@router.get("/ths_index")
async def ths_index(
    ts_code: str | None = Query(None, description="股票代码"),
    index_name: str | None = Query(None, description="概念/行业名称（模糊匹配）"),
    index_type: str | None = Query(None, description="N=概念, I=行业, R=地域"),
    limit: int = Query(500),
):
    """获取同花顺概念/行业成分股."""
    conditions: list[str] = []
    params: list[Any] = []
    if ts_code:
        conditions.append("ts_code = ?")
        params.append(ts_code)
    if index_name:
        conditions.append("index_name LIKE ?")
        params.append(f"%{index_name}%")
    if index_type:
        conditions.append("index_type = ?")
        params.append(index_type)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = f"SELECT * FROM ths_index{where} LIMIT {limit}"
    try:
        df = _query(sql, params)
    except Exception:
        return []
    return _df_to_records(df)


@router.get("/ths_index/concepts")
async def ths_concepts(
    ts_code: str = Query(..., description="股票代码"),
):
    """获取某只股票的所有概念标签."""
    df = _query(
        "SELECT index_name FROM ths_index WHERE ts_code = ? ORDER BY index_name",
        [ts_code],
    )
    return [str(r["index_name"]) for _, r in df.iterrows()] if not df.empty else []


@router.get("/broker_recommend/broker_history")
async def broker_history(
    broker: str = Query(..., description="券商名称"),
    limit: int = Query(12, description="返回最近 N 个月"),
):
    """券商历史命中率 — 过去 N 个月每月的推荐次数、上涨比例、平均收益."""
    months_df = _query(
        "SELECT DISTINCT month FROM broker_recommend WHERE broker = ? "
        "ORDER BY month DESC LIMIT ?",
        [broker, limit],
    )
    if months_df.empty:
        return []

    results = []
    for _, row in months_df.iterrows():
        m = str(row["month"])
        ms = f"{m[:4]}-{m[4:6]}-01"
        y, mi = int(m[:4]), int(m[4:6])
        ns = f"{y}-{mi + 1:02d}-01" if mi < 12 else f"{y + 1}-01-01"

        try:
            perf = _query(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN (l.close - f.close) > 0 THEN 1 ELSE 0 END) AS up_cnt, "
                "AVG((l.close - f.close) / NULLIF(f.close, 0) * 100) AS avg_r "
                "FROM broker_recommend br "
                "JOIN (SELECT ts_code, close FROM daily WHERE (ts_code, trade_date) IN ("
                "  SELECT ts_code, MIN(trade_date) FROM daily "
                "  WHERE trade_date >= ? GROUP BY ts_code)) f ON br.ts_code = f.ts_code "
                "JOIN (SELECT ts_code, close FROM daily WHERE (ts_code, trade_date) IN ("
                "  SELECT ts_code, MAX(trade_date) FROM daily "
                "  WHERE trade_date < ? GROUP BY ts_code)) l ON br.ts_code = l.ts_code "
                "WHERE br.broker = ? AND br.month = ?",
                [ms, ns, broker, m],
            )
            if not perf.empty:
                r = perf.iloc[0]
                results.append({
                    "month": m,
                    "total": int(r["total"]),
                    "up_cnt": int(r["up_cnt"]) if r["up_cnt"] is not None else 0,
                    "hit_rate": round(int(r["up_cnt"]) / int(r["total"]) * 100, 1) if r["total"] and r["up_cnt"] is not None and int(r["total"]) > 0 else None,
                    "avg_return": round(float(r["avg_r"]), 2) if r["avg_r"] is not None else None,
                })
        except Exception:
            pass
    return results


@router.get("/broker_recommend/broker_profile")
async def broker_profile(
    broker: str = Query(..., description="券商名称"),
    month: str = Query(..., description="月份 YYYYMM"),
):
    """券商画像 — 集中度、行业偏好、重复推荐率."""
    # 该月推荐
    recs = _query(
        "SELECT br.ts_code, br.name, sb.industry, sb.area "
        "FROM broker_recommend br "
        "LEFT JOIN stock_basic sb ON br.ts_code = sb.ts_code "
        "WHERE br.broker = ? AND br.month = ?",
        [broker, month],
    )
    if recs.empty:
        return {"error": "无推荐数据"}

    total = len(recs)

    # 集中度：每只股票被推荐次数排行
    stock_cnt = recs.groupby("ts_code").size().sort_values(ascending=False)
    top3_ratio = round(stock_cnt.head(3).sum() / total * 100, 1) if total > 0 else 0

    # 行业偏好
    industry_dist = recs["industry"].value_counts().head(5).to_dict()
    industry_dist = {k: int(v) for k, v in industry_dist.items() if k and str(k) != "nan"}

    # 重复率：本月推荐的票中，之前也被该券商推荐过的比例
    repeated = 0
    for _, r in recs.iterrows():
        prev = _query(
            "SELECT COUNT(*) FROM broker_recommend WHERE broker = ? AND ts_code = ? AND month < ?",
            [broker, r["ts_code"], month],
        )
        if not prev.empty and int(prev.iloc[0, 0]) > 0:
            repeated += 1

    repeat_rate = round(repeated / total * 100, 1) if total > 0 else 0

    return {
        "broker": broker,
        "month": month,
        "total": total,
        "unique_stocks": len(stock_cnt),
        "top3_ratio": top3_ratio,
        "repeat_rate": repeat_rate,
        "industry_dist": industry_dist,
    }


@router.get("/broker_recommend/benchmark")
async def broker_benchmark(
    month: str = Query(..., description="月份 YYYYMM"),
):
    """基准对比 — 券商推荐 vs 沪深300 同期收益."""
    ms = f"{month[:4]}-{month[4:6]}-01"
    y, mi = int(month[:4]), int(month[4:6])
    ns = f"{y}-{mi + 1:02d}-01" if mi < 12 else f"{y + 1}-01-01"

    # 沪深300 月收益
    bench_return = None
    try:
        bf = _query(
            "SELECT close FROM daily WHERE ts_code = '000300.SH' AND trade_date >= ? "
            "ORDER BY trade_date ASC LIMIT 1", [ms]
        )
        bl = _query(
            "SELECT close FROM daily WHERE ts_code = '000300.SH' AND trade_date < ? "
            "ORDER BY trade_date DESC LIMIT 1", [ns]
        )
        if not bf.empty and not bl.empty:
            bench_return = round((float(bl.iloc[0, 0]) - float(bf.iloc[0, 0])) / float(bf.iloc[0, 0]) * 100, 2)
    except Exception:
        pass

    # 各券商超额收益
    brokers = _query(
        "SELECT broker FROM broker_recommend WHERE month = ? GROUP BY broker", [month]
    )
    results = []
    for _, row in brokers.iterrows():
        bn = row["broker"]
        try:
            perf = _query(
                "SELECT AVG((l.close - f.close) / NULLIF(f.close, 0) * 100) AS avg_r "
                "FROM broker_recommend br "
                "JOIN (SELECT ts_code, close FROM daily WHERE (ts_code, trade_date) IN ("
                "  SELECT ts_code, MIN(trade_date) FROM daily "
                "  WHERE trade_date >= ? GROUP BY ts_code)) f ON br.ts_code = f.ts_code "
                "JOIN (SELECT ts_code, close FROM daily WHERE (ts_code, trade_date) IN ("
                "  SELECT ts_code, MAX(trade_date) FROM daily "
                "  WHERE trade_date < ? GROUP BY ts_code)) l ON br.ts_code = l.ts_code "
                "WHERE br.broker = ? AND br.month = ?",
                [ms, ns, bn, month],
            )
            avg_r = round(float(perf.iloc[0, 0]), 2) if not perf.empty and perf.iloc[0, 0] is not None else None
            excess = round(avg_r - bench_return, 2) if avg_r is not None and bench_return is not None else None
            results.append({
                "broker": bn,
                "avg_return": avg_r,
                "benchmark_return": bench_return,
                "excess_return": excess,
            })
        except Exception:
            results.append({"broker": bn, "avg_return": None, "benchmark_return": bench_return, "excess_return": None})

    results.sort(key=lambda x: x["excess_return"] if x["excess_return"] is not None else -999, reverse=True)
    return {"benchmark": "沪深300", "benchmark_return": bench_return, "brokers": results}


@router.get("/broker_recommend/consensus")
async def broker_consensus(
    month: str = Query(..., description="月份 YYYYMM"),
):
    """共识效应 — 被越多券商推荐，收益越高吗？"""
    ms = f"{month[:4]}-{month[4:6]}-01"
    y, mi = int(month[:4]), int(month[4:6])
    ns = f"{y}-{mi + 1:02d}-01" if mi < 12 else f"{y + 1}-01-01"

    df = _query(
        "SELECT br.ts_code, br.name, COUNT(DISTINCT br.broker) AS broker_cnt, "
        "AVG((l.close - f.close) / NULLIF(f.close, 0) * 100) AS avg_return, "
        "COUNT(*) AS total_rec "
        "FROM broker_recommend br "
        "JOIN (SELECT ts_code, close FROM daily WHERE (ts_code, trade_date) IN ("
        "  SELECT ts_code, MIN(trade_date) FROM daily "
        "  WHERE trade_date >= ? GROUP BY ts_code)) f ON br.ts_code = f.ts_code "
        "JOIN (SELECT ts_code, close FROM daily WHERE (ts_code, trade_date) IN ("
        "  SELECT ts_code, MAX(trade_date) FROM daily "
        "  WHERE trade_date < ? GROUP BY ts_code)) l ON br.ts_code = l.ts_code "
        "WHERE br.month = ? "
        "GROUP BY br.ts_code, br.name ORDER BY broker_cnt DESC",
        [ms, ns, month],
    )
    if df.empty:
        return []

    records = _df_to_records(df)

    # 按券商数分组统计
    groups = {"1家独推": [], "2-3家": [], "5家以上共识": []}
    for r in records:
        cnt = r.get("broker_cnt", 0)
        ret = r.get("avg_return")
        if cnt == 1:
            groups["1家独推"].append(ret)
        elif cnt <= 4:
            groups["2-3家"].append(ret)
        else:
            groups["5家以上共识"].append(ret)

    result = []
    for label, returns in groups.items():
        valid = [x for x in returns if x is not None]
        result.append({
            "group": label,
            "count": len(returns),
            "avg_return": round(sum(valid) / len(valid), 2) if valid else None,
        })

    # 散点数据
    scatter = [{"broker_cnt": r["broker_cnt"], "avg_return": r["avg_return"],
                 "ts_code": r["ts_code"], "name": r.get("name", "")} for r in records]

    return {"groups": result, "scatter": scatter}


@router.get("/broker_recommend/lagged")
async def broker_lagged(
    ts_code: str = Query(..., description="股票代码"),
    month: str = Query(..., description="推荐月份 YYYYMM"),
):
    """滞后表现 — 推荐后 1/3/6 个月收益."""
    ms = f"{month[:4]}-{month[4:6]}-01"
    y, mi = int(month[:4]), int(month[4:6])

    def _next_month(ym: tuple[int, int], offset: int) -> str:
        ny, nm = ym[0], ym[1] + offset
        while nm > 12:
            ny += 1
            nm -= 12
        return f"{ny}-{nm:02d}-01"

    cuts = [
        ("1个月", _next_month((y, mi), 1)),
        ("3个月", _next_month((y, mi), 3)),
        ("6个月", _next_month((y, mi), 6)),
    ]

    try:
        first = _query(
            "SELECT close FROM daily WHERE ts_code = ? AND trade_date >= ? "
            "ORDER BY trade_date ASC LIMIT 1", [ts_code, ms]
        )
        if first.empty:
            return {"error": "无数据"}
        fc = float(first.iloc[0, 0])

        result = {"ts_code": ts_code, "month": month, "first_close": round(fc, 2), "returns": []}
        for label, cutoff in cuts:
            ldf = _query(
                "SELECT close FROM daily WHERE ts_code = ? AND trade_date < ? "
                "ORDER BY trade_date DESC LIMIT 1", [ts_code, cutoff]
            )
            if not ldf.empty:
                lc = float(ldf.iloc[0, 0])
                ret = round((lc - fc) / fc * 100, 2)
            else:
                ret = None
            result["returns"].append({"period": label, "return": ret})
        return result
    except Exception as e:
        return {"error": str(e)}
