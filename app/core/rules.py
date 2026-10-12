"""业务规则层：把散落在文档里的业务规则固化成代码

这些规则来自实测核对（见《查漏补缺报告.md》），不是猜测：
- 地市抽取：从活动名称提取地市（已验证 716/716 正确）
- 地市排序：茂名排在湛江上面
- 小数位：C-H 列 2 位，I 列起 3 位
- 环比：当月 - 上月
"""
from __future__ import annotations

from typing import Any

from .config import CITIES, CITY_ORDER_OVERRIDE


def extract_city(activity_name: Any) -> str:
    """从「活动名称」提取地市（派生列 `地市抽取`）

    实测规则：活动名称如「下发珠海-2026Q3存量VPN群招工扩群商机营销活动」
    注意「深圳」与「深圳市」两种写法都存在，本函数按地市名做包含匹配。
    """
    if activity_name is None:
        return ""
    text = str(activity_name)
    for city in CITIES:
        if city in text:
            return city
    return ""


def normalize_city(raw: Any) -> str:
    """地市名归一化

    实测发现源数据写法不一致：
      · 调度工单样例：`深圳市` / `广州市`（带「市」字）
      · 输出目标表：  `深圳` / `广州`（不带）
    → 统一去掉「市」字后缀，便于跨表关联。
    """
    if raw is None:
        return ""
    s = str(raw).strip().replace("\t", "").strip()
    if not s:
        return ""
    # 去掉常见后缀
    for suffix in ("市", "分公司", "公司"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    return s.strip()


def city_sort_key(city: Any) -> tuple:
    """地市排序键：茂名需排在湛江上面

    实现方式：给茂名一个略小于湛江的排序值。
    """
    name = str(city or "").strip()
    if name in CITY_ORDER_OVERRIDE:
        # 茂名 -> 取湛江的位置（排在其上方）
        target = CITY_ORDER_OVERRIDE[name]
        try:
            return (CITIES.index(target) - 0.5,)
        except ValueError:
            return (999,)
    try:
        return (float(CITIES.index(name)),)
    except ValueError:
        return (999,)


def sort_rows_by_city(rows: list[list[Any]], city_col: int = 0) -> list[list[Any]]:
    """按地市排序（含茂名特殊规则），保留首行表头"""
    if not rows:
        return rows
    header, body = rows[0], rows[1:]
    body = sorted(body, key=lambda r: city_sort_key(r[city_col] if len(r) > city_col else ""))
    return [header] + body


def mom_change(current: Any, previous: Any) -> float | None:
    """环比 = 当月 - 上月"""
    try:
        if current in (None, "", "-") or previous in (None, "", "-"):
            return None
        return round(float(current) - float(previous), 4)
    except (TypeError, ValueError):
        return None


def as_float(value: Any) -> float | None:
    """安全转 float"""
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compute_rate(numerator: Any, denominator: Any, digits: int = 4) -> float | None:
    """安全算率：分母为 0 或空时返回 None"""
    n, d = as_float(numerator), as_float(denominator)
    if n is None or d in (None, 0):
        return None
    return round(n / d, digits)


# ============================================================
# 活动类指标的**标准口径**（业务方明确约定）
# ------------------------------------------------------------
#   回单率 = 回单量 ÷ 派单量
#   转化率 = 转化量 ÷ 派单量
#
# ⚠️ 背景说明：
#   导出数据源时**本来就带「派单 / 回单 / 转化」三列**，
#   平台**不需要**从工单明细里推算，直接用这三列算率即可。
#
#   （历史遗留：个别历史表存在口径不一致——个别活动用「总工单数」做分母。
#     平台策略：**源有值就原样保留**；源为空时才用上面的标准公式兜底。）
# ============================================================

RATE_FORMULAS = {
    "回单率": ("回单量", "派单量"),
    "转化率": ("转化量", "派单量"),
}


def activity_rate(metric: str, rec: dict, digits: int = 10) -> Any:
    """按标准口径计算活动比率

    metric: 「回单率」或「转化率」
    rec:    含「派单量/回单量/转化量」的字典

    规则：
      · 源里已有值     → 原样保留（尊重人工口径）
      · 分母为空/0     → "-"
      · 分子为空       → "-"
      · 分子为 0       → "-"
      · 其他           → 分子 ÷ 分母，保留 digits 位
    """
    raw = rec.get(metric)
    if raw is not None and str(raw).strip() not in ("", "-", "None", "nan"):
        return raw

    pair = RATE_FORMULAS.get(metric)
    if not pair:
        return "-"
    num_key, den_key = pair

    n, d = as_float(rec.get(num_key)), as_float(rec.get(den_key))
    if d in (None, 0) or n is None or n == 0:
        return "-"
    return round(n / d, digits)
