"""模块② 数据分析 —— 落责数据更新（唐丽流程 · 主输出⑥）

完整还原业务口径（见《月报-客户落责加工口径.md》）

输入（2 份源，均须本批次）：
  源A 【数据来源-月度更新】21各地市落责情况统计及排名.xlsx
       sheet: 地市总体落责 / 集团规上 / 专精特新
  源B 【数据来源-月度更新】 月度收入正增长客户数量.xlsx

源A 结构（每个 sheet 同构）：
  行1/2/3 = 三层表头（客户分级 | 行客/商客/综合营服 | 落责数量…）
  数据区：每个「地市/全省维度」占 5 行（卓越/战略/核心/主要/普通）
  全省有两个维度，**取维度二**（产权对应地市是否有实际业务收入判断）

源A 列（0-based）：
  0 地市 | 1 客户分级 | 2 落责准确率(小数)
  3-5 行客(落责数/直销/有客经) | 6-8 商客 | 9-11 综合营服
  12 总计·落责数量 | 13 总计·直销落责数量 | 14 总计·有客户经理数
  15 总计·落责准确数 | 16 总计·有客户经理占比(小数)

派生公式（⚠️ 关键）：
  落责准确率%      = 准确数 ÷ 落责数 × 100
  有客户经理占比%  = 有客经数 ÷ 直销落责数 × 100
  正增长客户占比%  = 正增长数 ÷ 落责数 × 100
  小计准确率%      = (战略准确数+核心准确数) ÷ (战略落责数+核心落责数) × 100
                     ⚠️ 不是两段准确率的平均！

输出：月报-客户落责数据处理.xlsx
  · 全量统计（含 PPT材料 区）
  · 全量收入正增长统计（含 PPT材料 区）
  · 集团规上统计（含 集团规上PPT材料 区）
  · 专精特新统计（含 专精特新PPT材料 区）
  · 三块 PPT 材料区位于原工作表右侧
"""
from __future__ import annotations

import re
import math
from pathlib import Path
from typing import Any

from ..core import tables
from ..core.logger import get_logger
from ..core.rules import as_float
from ..core.runner import TaskResult, registry
from openpyxl import load_workbook

log = get_logger("data.lz")

MODULE = "数据分析"

OUT_FILE = "月报-客户落责数据处理.xlsx"
TEMPLATE_FILE = "落责-【每月更新】落责材料数据处理.xlsx"

# 客户分级顺序
GRADES = ["卓越", "战略", "核心", "主要", "普通"]

# PPT 只展示 3 级
PPT_GRADES = ["卓越", "战略", "核心"]

# 地市固定顺序（与源一致）
CITY_ORDER = [
    "深圳", "广州", "东莞", "佛山", "中山", "惠州", "江门", "珠海", "汕头", "揭阳",
    "潮州", "汕尾", "茂名", "湛江", "阳江", "云浮", "清远", "肇庆", "梅州", "韶关",
    "河源", "省政企客户事业部",
]
# ⚠️ 最后一行的显示名按 PPT 页码不同（实测自最终 PPT）
CITY_ALIAS_P1 = {"省政企客户事业部": "政企"}       # PPT 第1页
CITY_ALIAS_P23 = {"省政企客户事业部": "省管客户"}   # PPT 第2/3页

# ⚠️ 源间地市名不一致：源B 叫「省政企客户」，源A 叫「省政企客户事业部」
GROW_CITY_ALIAS = {"省政企客户": "省政企客户事业部"}

STAT_SHEETS = {"地市总体落责": GRADES, "集团规上": ["战略", "核心"], "专精特新": ["战略", "核心"]}
STAT_FIELDS = ("落责数量", "落责准确客户数量", "直销落责数量",
               "有客户经理服务的客户数量", "落责准确率", "有客户经理占比")

# 源A 列索引
C_CITY, C_GRADE, C_RATE = 0, 1, 2
C_TOTAL_RESP, C_TOTAL_DIRECT, C_TOTAL_MGR = 12, 13, 14
C_TOTAL_ACCURATE, C_TOTAL_MGR_RATE = 15, 16


def _find(inbox: Path, *keywords: str) -> Path | None:
    if not inbox.exists():
        return None
    matches = (p for p in inbox.iterdir()
               if p.is_file() and p.suffix.lower() in (".xlsx", ".xlsm")
               and not p.name.startswith(("~$", ".~", "."))
               and any(kw in p.name for kw in keywords))
    return max(matches, key=lambda p: (p.stat().st_mtime_ns, p.name), default=None)


def _num(v: Any) -> float | None:
    """转数字

    · 空 / None → None
    · "/" 等占位符 → 0（源表里卓越级无数据时写 "/"）
    · 其他 → float
    """
    if v is None:
        return None
    s = str(v).strip()
    if not s or s == "None":
        return None
    if s in ("/", "-", "—", "－"):
        return 0
    return as_float(v)


def _rate(num: Any, den: Any, digits: int = 10) -> Any:
    """率 = 分子/分母×100

    规则（实测自案例）：
      · 分母为 0 或缺失 → 0（案例写 0，不是空）
      · 分子缺失        → 0
      · 其他            → 保留 10 位
    """
    n, d = _num(num), _num(den)
    if d in (None, 0) or n is None:
        return 0
    return round(n / d * 100, digits)


def _parse_stat_sheet(rows: list[list[Any]]) -> dict[str, dict[str, dict]]:
    """解析源A的一个 sheet → {地市: {分级: {字段: 值}}}

    数据区从「客户分级」列出现 卓越/战略/… 的位置开始。
    每个地市/全省维度占连续 5 行。
    """
    out: dict[str, dict[str, dict]] = {}
    cur_city: str | None = None

    for r in rows:
        if not any(c is not None and str(c).strip() for c in r):
            continue
        city_cell = str(r[C_CITY]).strip() if len(r) > C_CITY and r[C_CITY] is not None else ""
        grade_cell = str(r[C_GRADE]).strip() if len(r) > C_GRADE and r[C_GRADE] is not None else ""

        if city_cell:
            cur_city = city_cell.replace("\n", "").strip()

        if grade_cell not in GRADES or not cur_city:
            continue

        rec = {
            "落责数量": _num(r[C_TOTAL_RESP] if len(r) > C_TOTAL_RESP else None),
            "落责准确客户数量": _num(r[C_TOTAL_ACCURATE] if len(r) > C_TOTAL_ACCURATE else None),
            "落责准确率": _num(r[C_RATE] if len(r) > C_RATE else None),
            "直销落责数量": _num(r[C_TOTAL_DIRECT] if len(r) > C_TOTAL_DIRECT else None),
            "有客户经理服务的客户数量": _num(r[C_TOTAL_MGR] if len(r) > C_TOTAL_MGR else None),
            "有客户经理占比": _num(r[C_TOTAL_MGR_RATE] if len(r) > C_TOTAL_MGR_RATE else None),
        }
        out.setdefault(cur_city, {})[grade_cell] = rec

    return out


def _pick_province_dimension(data: dict, dimension: str) -> dict:
    """挑出全省指定维度；模板同时展示维度一、维度二。"""
    for key in data:
        if "全省" in key and f"维度{dimension}" in key:
            return data[key]
    return {}


def _pick_province(data: dict) -> dict:
    """挑出「全省维度二」。

    源里全省会以「全省（维度一：产权与注册地判断）」「全省（维度二：产权对应地市是否有实际业务收入判断）」
    出现。取维度二。
    """
    selected = _pick_province_dimension(data, "二")
    if selected:
        return selected
    # 兜底：任何含"全省"的
    for k in data:
        if "全省" in k:
            return data[k]
    return {}


def _norm_grow(rows: list[list[Any]]) -> dict[str, dict[str, float]]:
    """解析源B → {地市: {分级: 正增长数}}"""
    out: dict[str, dict[str, float]] = {}
    for r in rows:
        cells = [str(c).strip() if c is not None else "" for c in r]
        # 地市在「客户所属地市」列（列1），分级从列2起
        if len(cells) < 7:
            continue
        city = cells[1]
        if not city or city in ("客户所属地市",):
            continue
        city = GROW_CITY_ALIAS.get(city, city)   # 名称归一化
        for i, g in enumerate(GRADES):
            v = _num(r[2 + i] if len(r) > 2 + i else None)
            if v is not None:
                out.setdefault(city, {})[g] = v
    return out


class SourceDataError(ValueError):
    """源工作簿的结构或必需数据不符合本任务要求。"""


def _header_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _validate_stat_sheet(rows: list[list[Any]], sheet: str) -> dict[str, dict[str, dict]]:
    """确认列位、地市和所有会进入成果的数值都齐全。"""
    checks = (
        (0, "地市", True), (1, "客户分级", True), (2, "落责准确率", False),
        (3, "落责数量", False), (4, "直销落责数量", False),
        (5, "有客户经理数", False), (6, "落责数量", False),
        (7, "直销落责数量", False), (8, "有客户经理数", False),
        (9, "落责数量", False), (10, "直销落责数量", False),
        (11, "有客户经理数", False),
        (12, "落责数量", False), (13, "直销落责数量", False),
        (14, "有客户经理数", False), (15, "落责准确数", False),
        (16, "有客户经理占比", False),
        (17, "落责不准确", False), (18, "未落责", False),
    )
    for column, label, exact in checks:
        found = any(((_header_text(row[column]) == label) if exact
                     else (label in _header_text(row[column])))
                    for row in rows[:4] if len(row) > column)
        if not found:
            raise SourceDataError(f"「{sheet}」模板不匹配：第 {column + 1} 列缺少表头「{label}」")
    for column, label in ((3, "行客"), (6, "商客"), (9, "综合营服"), (12, "总计")):
        value = rows[2][column] if len(rows) > 2 and len(rows[2]) > column else None
        if label not in _header_text(value):
            raise SourceDataError(f"「{sheet}」模板不匹配：第 {column + 1} 列应为「{label}」")

    data = _parse_stat_sheet(rows)
    provinces = {}
    for dimension in ("一", "二"):
        matches = [city for city in data if "全省" in city and f"维度{dimension}" in city]
        if len(matches) != 1:
            raise SourceDataError(f"「{sheet}」缺少唯一的全省维度{dimension}数据")
        provinces[dimension] = matches[0]

    # 解析器遇到重复行会覆盖旧值，因此在此处先拒绝重复地市与分级。
    seen: set[tuple[str, str]] = set()
    city = ""
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            city = str(row[0]).replace("\n", "").strip()
        grade = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        if city and grade in GRADES:
            key = (city, grade)
            if key in seen:
                raise SourceDataError(f"「{sheet}」有重复数据：{city}/{grade}")
            seen.add(key)
            if (city in CITY_ORDER or city in provinces.values()) and grade in STAT_SHEETS[sheet]:
                fields = {2: "落责准确率", 12: "落责数量", 13: "直销落责数量",
                          14: "有客户经理服务的客户数量", 15: "落责准确客户数量",
                          16: "有客户经理占比", 17: "落责不准确", 18: "未落责"}
                for column in range(2, 19):
                    field = fields.get(column, f"第 {column + 1} 列")
                    raw = row[column] if len(row) > column else None
                    value = as_float(raw)
                    if column in (2, 16) and str(raw).strip() == "/":
                        denominator = row[12 if column == 2 else 13]
                        if as_float(denominator) == 0:
                            continue  # 分母为 0 时，源表用“/”表示比率不适用。
                    if value is None or not math.isfinite(value):
                        raise SourceDataError(f"「{sheet}」的「{city}/{grade}/{field}」缺少有效数值")
                    if value < 0 or (column in (2, 16) and value > 1):
                        raise SourceDataError(f"「{sheet}」的「{city}/{grade}/{field}」数值超出范围")

    for city in [provinces["一"], provinces["二"], *CITY_ORDER]:
        if city not in data:
            raise SourceDataError(f"「{sheet}」缺少地市「{city}」的数据")
        for grade in STAT_SHEETS[sheet]:
            rec = data[city].get(grade)
            if rec is None:
                raise SourceDataError(f"「{sheet}」缺少「{city}/{grade}」数据")
            for field in STAT_FIELDS:
                value = rec.get(field)
                if value is None or not math.isfinite(value):
                    raise SourceDataError(f"「{sheet}」的「{city}/{grade}/{field}」缺少有效数值")
                if value < 0 or (field in ("落责准确率", "有客户经理占比") and value > 1):
                    raise SourceDataError(f"「{sheet}」的「{city}/{grade}/{field}」数值超出范围")
    return data


def _validate_grow_sheet(rows: list[list[Any]]) -> dict[str, dict[str, float]]:
    expected_header = ["序号", "客户所属地市", *GRADES]
    header = [_header_text(value) for value in (rows[0][:7] if rows else [])]
    if header != expected_header:
        raise SourceDataError("收入正增长文件模板不匹配：首行应为「序号、客户所属地市、卓越、战略、核心、主要、普通」")

    by_city: dict[str, list[Any]] = {}
    for row in rows[1:]:
        city = GROW_CITY_ALIAS.get(str(row[1]).strip(), str(row[1]).strip()) if len(row) > 1 and row[1] is not None else ""
        if city in ("全省", *CITY_ORDER):
            if city in by_city:
                raise SourceDataError(f"收入正增长文件有重复地市「{city}」")
            by_city[city] = row

    for city in ["全省", *CITY_ORDER]:
        row = by_city.get(city)
        if row is None:
            raise SourceDataError(f"收入正增长文件缺少「{city}」的数据")
        for index, grade in enumerate(GRADES, 2):
            raw = row[index] if len(row) > index else None
            value = as_float(raw)
            if value is None or not math.isfinite(value):
                raise SourceDataError(f"收入正增长文件的「{city}/{grade}」缺少有效数值")
            if value < 0:
                raise SourceDataError(f"收入正增长文件的「{city}/{grade}」不能为负数")
    return _norm_grow(rows)


def _build_section(stat: dict, grow: dict, grade: str) -> dict:
    """组装单个（地市 × 分级）的 6 个指标"""
    rec = stat.get(grade, {})
    resp = rec.get("落责数量")
    accurate = rec.get("落责准确客户数量")
    direct = rec.get("直销落责数量")
    mgr = rec.get("有客户经理服务的客户数量")

    # 优先用源给的率（×100），否则按公式算
    rate = rec.get("落责准确率")
    rate_pct = round(rate * 100, 10) if rate is not None else _rate(accurate, resp)

    mrate = rec.get("有客户经理占比")
    mrate_pct = round(mrate * 100, 10) if mrate is not None else _rate(mgr, direct)

    gr = grow.get(grade)
    gratio = _rate(gr, resp)

    # ⚠️ 0 要保留（不是空）；只有真正的缺失才输出空
    def _v(x: Any) -> Any:
        return 0 if x == 0 else (x if x is not None else "")

    return {
        "落责数量": _v(resp),
        "落责准确客户数量": _v(accurate),
        "落责准确率%": rate_pct,
        "直销落责数量": _v(direct),
        "有客户经理服务的客户数量": _v(mgr),
        "有客户经理占比%": mrate_pct,
        "收入正增长客户数量": _v(gr),
        "正增长客户占比%": gratio,
    }


def _build_wide_row(city: str, stat: dict, grow: dict) -> list[Any]:
    """一行 = 地市 + 5 分级 × 6 指标（全量统计口径）"""
    row: list[Any] = [city]
    for g in GRADES:
        s = _build_section(stat, grow, g)
        row += [s[k] for k in (
            "落责数量", "落责准确客户数量", "落责准确率%",
            "直销落责数量", "有客户经理服务的客户数量", "有客户经理占比%",
        )]
    return row


def _build_ppt_row_full(city: str, stat: dict, grow: dict) -> list[Any]:
    """PPT 第1页行：地市 + 3 分级 × 5 指标"""
    row: list[Any] = [city]
    for g in PPT_GRADES:
        s = _build_section(stat, grow, g)
        row += [s[k] for k in (
            "落责数量", "落责准确率%", "有客户经理占比%",
            "收入正增长客户数量", "正增长客户占比%",
        )]
    return row


def _subtotal(stat: dict, grades: list[str]) -> dict[str, Any]:
    """小计：⚠️ 准确率 = 准确数之和 ÷ 落责数之和（不是平均）"""
    resp = acc = direct = mgr = 0.0
    has = False
    for g in grades:
        r = stat.get(g, {})
        if r.get("落责数量") is not None:
            has = True
            resp += r.get("落责数量") or 0
            acc += r.get("落责准确客户数量") or 0
            direct += r.get("直销落责数量") or 0
            mgr += r.get("有客户经理服务的客户数量") or 0
    if not has:
        return {"落责数量": "", "落责准确率%": "", "有客户经理": "", "有客户经理占比%": ""}
    return {
        "落责数量": round(resp, 2),
        "落责准确率%": round(acc / resp * 100, 10) if resp else 0,
        "有客户经理": round(mgr, 2),
        "有客户经理占比%": round(mgr / direct * 100, 10) if direct else 0,
    }


def _build_ppt_row_simple(city: str, stat: dict, grades: list[str] = ("战略", "核心")) -> list[Any]:
    """PPT 第2/3页行：地市 + 战略(4) + 核心(4) + 小计(4)"""
    row: list[Any] = [city]
    for g in grades:
        r = stat.get(g, {})
        rate = r.get("落责准确率")
        rate_pct = round(rate * 100, 10) if rate is not None else _rate(
            r.get("落责准确客户数量"), r.get("落责数量"))
        mrate = r.get("有客户经理占比")
        mrate_pct = round(mrate * 100, 10) if mrate is not None else _rate(
            r.get("有客户经理服务的客户数量"), r.get("直销落责数量"))
        row += [
            r.get("落责数量") if r.get("落责数量") is not None else "",
            rate_pct,
            r.get("有客户经理服务的客户数量") if r.get("有客户经理服务的客户数量") is not None else "",
            mrate_pct,
        ]
    st = _subtotal(stat, list(grades))
    row += [st["落责数量"], st["落责准确率%"], st["有客户经理"], st["有客户经理占比%"]]
    return row


def _empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


@registry.register(
    key="monthly_luozhe",
    name="落责数据处理",
    module=MODULE,
    description="从 21地市落责统计 + 月度收入正增长，沿用原模板生成 4 张工作表及表内 PPT 材料区",
    source_type="semi",
    source_note="""
【两份源文件（请确认属于本批次）】
  1. 【数据来源-月度更新】21各地市落责情况统计及排名（地市总体、集团规上、专精特新）
     · 需含 3 个 sheet：地市总体落责 / 集团规上 / 专精特新
     · 每个 sheet 内：地市 × 客户分级（卓越/战略/核心/主要/普通）
  2. 【数据来源-月度更新】 月度收入正增长客户数量
     · 列：序号 | 客户所属地市 | 卓越 | 战略 | 核心 | 主要 | 普通

【取数路径】CDAP 取数（按最新账期）

【⚠️ 关键口径】
  · 全省取「维度二」（产权对应地市是否有实际业务收入判断），不是维度一
  · 地市顺序：深圳/广州/东莞/佛山/中山/惠州/江门/珠海/汕头/揭阳/
             潮州/汕尾/茂名/湛江/阳江/云浮/清远/肇庆/梅州/韶关/河源/省政企客户事业部
  · 落责准确率%     = 准确数 ÷ 落责数 × 100
  · 有客户经理占比% = 有客经数 ÷ 直销落责数 × 100  ← 分母是「直销落责数量」
  · 正增长客户占比% = 正增长数 ÷ 落责数 × 100
  · 小计准确率%     = (战略准确数+核心准确数) ÷ (战略落责数+核心落责数) × 100
                      ⚠️ 不是两段准确率的平均！
  · PPT 只展示 3 级（卓越/战略/核心），不含「主要/普通」

【上传要求】放入 data/inbox/，文件名分别含「落责」与「正增长」，不要求文件名带账期。同类文件重复上传时使用最新上传的一份；请人工核对统计期。缺少工作表、地市、分级或必需数值时会停止生成。

【输出模板】使用平台内置《落责材料数据处理》原工作簿，保留四张工作表、原始数据区、表头颜色、合并单元格和三块表内 PPT 材料区。模板结构缺失时直接报错。
""",
    outputs_desc=(
        "月报-客户落责数据处理.xlsx → "
        "sheet「全量统计」+「全量收入正增长统计」+「集团规上统计」+「专精特新统计」"
        "（含 3 块工作表内 PPT 材料区）"
    ),
    required_sources=[
        {"label": "21各地市落责情况统计及排名", "keywords": ["21各地市落责", "落责情况统计"], "extensions": [".xlsx", ".xlsm"]},
        {"label": "月度收入正增长客户数量", "keywords": ["收入正增长", "正增长客户数量"], "extensions": [".xlsx", ".xlsm"]},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def monthly_luozhe(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR

    f_stat = _find(inbox_path, "21各地市落责", "落责情况统计")
    f_grow = _find(inbox_path, "收入正增长", "正增长客户数量")

    missing = []
    if not f_stat:
        missing.append("21各地市落责情况统计及排名")
    if not f_grow:
        missing.append("月度收入正增长客户数量")
    if missing:
        return TaskResult(ok=False, message=f"缺少源文件：{'、'.join(missing)}")

    warnings: list[str] = []
    metrics: dict[str, Any] = {"源1": f_stat.name, "源2": f_grow.name}

    # ---------- 写出成果前校验两份源表 ----------
    try:
        available = set(tables.sheet_names(f_stat))
        absent = [sheet for sheet in STAT_SHEETS if sheet not in available]
        if absent:
            raise SourceDataError(f"落责文件缺少工作表：{'、'.join(absent)}")
        raw_stat = {sheet: _rows_of(f_stat, sheet) for sheet in STAT_SHEETS}
        parsed = {sheet: _validate_stat_sheet(raw_stat[sheet], sheet) for sheet in STAT_SHEETS}
        raw_grow = _rows_of(f_grow, None)
        grow = _validate_grow_sheet(raw_grow)
    except SourceDataError as e:
        return TaskResult(ok=False, message=f"源数据校验失败：{e}", metrics=metrics)
    except Exception as e:
        return TaskResult(ok=False, message=f"源数据校验失败：无法读取工作簿（{e}）", metrics=metrics)

    d_total = parsed["地市总体落责"]
    d_group = parsed["集团规上"]
    d_spec = parsed["专精特新"]

    # ---------- 全省（取维度二）----------
    prov_total = _pick_province(d_total)
    prov_group = _pick_province(d_group)
    prov_spec = _pick_province(d_spec)
    prov_grow = grow.get("全省", {})

    # ---------- 沿用原模板写入 ----------
    template = Path(__file__).resolve().parents[2] / "data" / "templates" / TEMPLATE_FILE
    try:
        if not template.is_file():
            raise SourceDataError(f"缺少落责材料模板：{template.name}")
        wb = load_workbook(template)
        _validate_output_template(wb)
        _fill_template_workbook(
            wb, raw_stat, raw_grow, grow, d_total, d_group, d_spec,
        )
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
        out = OUTPUT_DIR / OUT_FILE
        out.parent.mkdir(parents=True, exist_ok=True)
        wb.save(out)
        wb.close()
    except SourceDataError as e:
        return TaskResult(ok=False, message=f"输出模板校验失败：{e}", metrics=metrics)
    except Exception as e:
        return TaskResult(ok=False, message=f"输出模板处理失败：{e}", metrics=metrics)

    metrics.update({
        "全量统计": "152 行 × 32 列",
        "全量收入正增长统计": "56 行 × 63 列",
        "集团规上统计": "82 行 × 39 列",
        "专精特新统计": "82 行 × 39 列",
    })

    return TaskResult(
        ok=True,
        message=(
            f"生成完成：按原模板更新 4 张工作表（含 3 块 PPT 材料区）"
            f"（{len(CITY_ORDER)} 个地市及 2 个全省维度）"
        ),
        outputs=[str(out)],
        metrics=metrics,
        warnings=warnings,
    )


def _validate_output_template(wb) -> None:
    """校验底板仍是用户提供的落责材料模板，避免静默写入错表。"""
    expected = ["全量统计", "全量收入正增长统计", "集团规上统计", "专精特新统计"]
    if wb.sheetnames != expected:
        raise SourceDataError(f"落责材料模板工作表不匹配，应为：{'、'.join(expected)}")
    shapes = {
        "全量统计": (152, 32, 12, 0, {"B3": "存量P码编码数量", "B31": "地市", "C31": "客户分级", "O31": "总计"}),
        "全量收入正增长统计": (56, 63, 16, 12, {"B5": "分公司", "AV4": "PPT材料", "AJ33": "序号", "AK33": "客户所属地市"}),
        "集团规上统计": (82, 39, 40, 6, {"B3": "（存量P码编码数量）", "AA4": "集团规上PPT材料", "B34": "地市", "AB5": "战略"}),
        "专精特新统计": (82, 39, 40, 6, {"B3": "（存量P码编码数量）", "AA4": "专精特新PPT材料", "B34": "地市", "AB5": "战略"}),
    }
    for name, (rows, cols, merges, rules, anchors) in shapes.items():
        ws = wb[name]
        if ws.max_row != rows or ws.max_column != cols:
            raise SourceDataError(f"模板工作表「{name}」尺寸不匹配")
        if len(ws.merged_cells.ranges) < merges or len(ws.conditional_formatting) < rules:
            raise SourceDataError(f"模板工作表「{name}」合并单元格或条件格式缺失")
        for cell, label in anchors.items():
            if str(ws[cell].value or "").strip() != label:
                raise SourceDataError(f"模板工作表「{name}」的 {cell} 缺少表头「{label}」")


def _index_source_rows(rows: list[list[Any]]) -> dict[tuple[str, str], list[Any]]:
    indexed: dict[tuple[str, str], list[Any]] = {}
    city = ""
    for row in rows:
        if row and row[0] is not None and str(row[0]).strip():
            city = str(row[0]).replace("\n", "").strip()
        grade = str(row[1]).strip() if len(row) > 1 and row[1] is not None else ""
        if city and grade in GRADES:
            indexed[(city, grade)] = row
    return indexed


def _copy_source_rows(ws, rows: list[list[Any]], start_row: int,
                      sheet: str) -> None:
    """把来源原始数据区搬到模板，D/E 均保留当期落责准确率。"""
    indexed = _index_source_rows(rows)
    province_one = next(city for city, _ in indexed if "全省" in city and "维度一" in city)
    province_two = next(city for city, _ in indexed if "全省" in city and "维度二" in city)
    cities = [province_one, province_two, *CITY_ORDER]
    grades = STAT_SHEETS[sheet]
    for city_index, city in enumerate(cities):
        for grade_index, grade in enumerate(grades):
            source = indexed[(city, grade)]
            target_row = start_row + city_index * len(grades) + grade_index
            # A:S → B:C、D:E、F:U。模板 D 是历史模板中的另一准确率列，必须同步本期值。
            if grade_index == 0:
                ws.cell(target_row, 2).value = source[0]
            ws.cell(target_row, 3).value = source[1]
            ws.cell(target_row, 4).value = source[2]
            ws.cell(target_row, 5).value = source[2]
            ws.cell(target_row, 6).value = source[3]
            for source_col in range(4, 19):
                ws.cell(target_row, source_col + 3).value = source[source_col]


def _copy_growth_rows(ws, rows: list[list[Any]]) -> None:
    by_city: dict[str, list[Any]] = {}
    for source in rows[1:]:
        if len(source) > 1 and source[1] is not None:
            city = GROW_CITY_ALIAS.get(str(source[1]).strip(), str(source[1]).strip())
            by_city[city] = source
    for offset, city in enumerate(["全省", *CITY_ORDER]):
        source = by_city[city]
        target_row = 34 + offset
        for source_col in range(7):
            ws.cell(target_row, 36 + source_col).value = source[source_col] if len(source) > source_col else None


def _simple_top_cells(stat: dict) -> tuple[list[Any], list[Any]]:
    """集团规上/专精特新顶部统计区 C:N 与 P:X。"""
    records = [stat.get(g, {}) for g in ("战略", "核心")]
    main: list[Any] = []
    for rec in records:
        resp = rec.get("落责数量") or 0
        rate = rec.get("落责准确率")
        mgr = rec.get("有客户经理服务的客户数量") or 0
        mrate = rec.get("有客户经理占比")
        main += [resp, round(rate * 100, 10) if rate is not None else _rate(rec.get("落责准确客户数量"), resp),
                 mgr, round(mrate * 100, 10) if mrate is not None else _rate(mgr, rec.get("直销落责数量"))]
    strategic, core = records
    resp = (strategic.get("落责数量") or 0) + (core.get("落责数量") or 0)
    accurate = (strategic.get("落责准确客户数量") or 0) + (core.get("落责准确客户数量") or 0)
    direct = (strategic.get("直销落责数量") or 0) + (core.get("直销落责数量") or 0)
    mgr = (strategic.get("有客户经理服务的客户数量") or 0) + (core.get("有客户经理服务的客户数量") or 0)
    main += [resp, round(accurate / resp * 100, 10) if resp else 0, mgr,
             round(mgr / direct * 100, 10) if direct else 0]
    aux = [resp - mgr,
           strategic.get("落责准确客户数量") or 0,
           core.get("落责准确客户数量") or 0,
           accurate, resp - accurate,
           accurate / resp if resp else 0,
           core.get("直销落责数量") or 0,
           strategic.get("直销落责数量") or 0,
           direct]
    return main, aux


def _write_simple_top(ws, row: int, stat: dict) -> None:
    main, aux = _simple_top_cells(stat)
    for col, value in enumerate(main, 3):
        ws.cell(row, col).value = value
    for col, value in enumerate(aux, 16):
        ws.cell(row, col).value = value


def _fill_template_workbook(
    wb, raw_stat: dict[str, list[list[Any]]], raw_grow: list[list[Any]],
    grow: dict, d_total: dict, d_group: dict, d_spec: dict,
) -> None:
    """在原模板固定坐标更新所有统计区、原始数据区和材料区。"""
    full = wb["全量统计"]
    growth = wb["全量收入正增长统计"]
    group = wb["集团规上统计"]
    spec = wb["专精特新统计"]

    # 原始数据区：保留模板表头、合并和样式，只替换数据值。
    _copy_source_rows(full, raw_stat["地市总体落责"], 33, "地市总体落责")
    _copy_growth_rows(growth, raw_grow)
    _copy_source_rows(group, raw_stat["集团规上"], 35, "集团规上")
    _copy_source_rows(spec, raw_stat["专精特新"], 35, "专精特新")

    # 全量统计顶部 C:AF。
    province_total = _pick_province_dimension(d_total, "二")
    records_total = [province_total] + [d_total[city] for city in CITY_ORDER]
    for row, stat in zip(range(5, 28), records_total):
        for col, value in enumerate(_wide_cells(stat, grow.get("全省" if row == 5 else CITY_ORDER[row - 6], {})), 3):
            full.cell(row, col).value = value

    # 全量收入正增长顶部 C:AP，以及右侧 AV:BK 材料区。
    for row, stat in zip(range(7, 30), records_total):
        city = "全省" if row == 7 else CITY_ORDER[row - 8]
        source_row = row + 27  # B34:AF56 对应顶部 B7:AF29。
        for col, value in enumerate(_wide_cells(stat, grow.get(city, {})), 3):
            growth.cell(source_row, col).value = value
        cells = _grow_cells(stat, grow.get(city, {}))
        for col, value in enumerate(cells, 3):
            growth.cell(row, col).value = value
        material = _ppt_full_cells(stat, grow.get(city, {}))
        for col, value in enumerate(material, 49):  # AW:BK
            growth.cell(row, col).value = value

    # 集团规上、专精特新：顶部同时显示全省维度一、维度二及 22 个地市。
    for ws, data in ((group, d_group), (spec, d_spec)):
        province_dim1 = _pick_province_dimension(data, "一")
        province_dim2 = _pick_province_dimension(data, "二")
        _write_simple_top(ws, 6, province_dim1)
        _write_simple_top(ws, 7, province_dim2)
        for row, city in zip(range(8, 30), CITY_ORDER):
            _write_simple_top(ws, row, data[city])
        # 材料区全省采用维度二，地市取对应城市数据。
        for row, stat in zip(range(7, 30), [province_dim2] + [data[city] for city in CITY_ORDER]):
            material = _ppt_simple_cells(stat)
            for col, value in enumerate(material, 28):  # AB:AM
                ws.cell(row, col).value = value


# ---------------------------------------------------------------- 辅助

def _rows_of(src: Path, sheet: str | None) -> list[list[Any]]:
    try:
        if sheet is None:
            names = tables.sheet_names(src)
            sheet = names[0] if names else None
        rows = tables.read_any(src, sheet) if sheet else []
    except Exception as e:
        raise SourceDataError(f"{src.name} 的工作表「{sheet}」无法读取：{e}") from e
    return [r for r in rows if any(c is not None and str(c).strip() for c in r)]


def _wide_cells(stat: dict, grow: dict) -> list[Any]:
    """5 分级 × 6 指标"""
    cells: list[Any] = []
    for g in GRADES:
        s = _build_section(stat, grow, g)
        cells += [s[k] for k in (
            "落责数量", "落责准确客户数量", "落责准确率%",
            "直销落责数量", "有客户经理服务的客户数量", "有客户经理占比%",
        )]
    return cells


def _ppt_full_cells(stat: dict, grow: dict) -> list[Any]:
    """3 分级 × 5 指标（PPT 第1页）"""
    cells: list[Any] = []
    for g in PPT_GRADES:
        s = _build_section(stat, grow, g)
        cells += [s[k] for k in (
            "落责数量", "落责准确率%", "有客户经理占比%",
            "收入正增长客户数量", "正增长客户占比%",
        )]
    return cells


def _ppt_simple_cells(stat: dict) -> list[Any]:
    """战略(4) + 核心(4) + 小计(4)"""
    cells: list[Any] = []
    for g in ("战略", "核心"):
        r = stat.get(g, {})
        rate = r.get("落责准确率")
        rate_pct = round(rate * 100, 10) if rate is not None else _rate(
            r.get("落责准确客户数量"), r.get("落责数量"))
        mrate = r.get("有客户经理占比")
        mrate_pct = round(mrate * 100, 10) if mrate is not None else _rate(
            r.get("有客户经理服务的客户数量"), r.get("直销落责数量"))
        cells += [
            r.get("落责数量") if r.get("落责数量") is not None else "",
            rate_pct,
            r.get("有客户经理服务的客户数量") if r.get("有客户经理服务的客户数量") is not None else "",
            mrate_pct,
        ]
    st = _subtotal(stat, ["战略", "核心"])
    cells += [st["落责数量"], st["落责准确率%"], st["有客户经理"], st["有客户经理占比%"]]
    return cells


def _build_simple_sheet(prov: dict, by_city: dict, grow: dict,
                        warnings: list[str], tag: str) -> list[list[Any]]:
    """集团规上统计 / 专精特新统计（战略 / 核心 / 小计 × 4 指标）"""
    head1: list[Any] = ["", "（存量P码编码数量）"]
    head2: list[Any] = ["", ""]
    for g in ("战略", "核心", "小计"):
        head1 += [g] + [""] * 3
        head2 += ["落责数量", "落责准确率%", "落责中有客户经理", "有客户经理占比%"]
    rows: list[list[Any]] = [head1, head2]
    rows.append(["", "全省"] + _simple_cells(prov))
    for city in CITY_ORDER:
        data = by_city.get(city)
        if not data:
            continue
        rows.append(["", CITY_ALIAS_P23.get(city, city)] + _simple_cells(data))
    return rows


def _simple_cells(stat: dict) -> list[Any]:
    cells: list[Any] = []
    for g in ("战略", "核心"):
        r = stat.get(g, {})
        rate = r.get("落责准确率")
        rate_pct = round(rate * 100, 10) if rate is not None else _rate(
            r.get("落责准确客户数量"), r.get("落责数量"))
        mrate = r.get("有客户经理占比")
        mrate_pct = round(mrate * 100, 10) if mrate is not None else _rate(
            r.get("有客户经理服务的客户数量"), r.get("直销落责数量"))
        cells += [
            r.get("落责数量") if r.get("落责数量") is not None else "",
            rate_pct,
            r.get("有客户经理服务的客户数量") if r.get("有客户经理服务的客户数量") is not None else "",
            mrate_pct,
        ]
    st = _subtotal(stat, ["战略", "核心"])
    cells += [st["落责数量"], st["落责准确率%"], st["有客户经理"], st["有客户经理占比%"]]
    return cells


def _build_grow_sheet(prov_total: dict, prov_grow: dict,
                      by_city: dict, grow: dict) -> list[list[Any]]:
    """全量收入正增长统计（明细 = 全量表 + 正增长 + 占比）"""
    head1: list[Any] = ["", "全量"]
    head2: list[Any] = ["", "分公司"]
    for g in GRADES:
        head1 += [g] + [""] * 7
        head2 += ["落责数量", "落责准确客户数量", "落责准确率%",
                  "直销落责数量", "有客户经理服务的客户数量", "有客户经理占比%",
                  "收入正增长客户数量", "正增长客户占比%"]
    rows: list[list[Any]] = [head1, head2]
    rows.append(["", "全省"] + _grow_cells(prov_total, prov_grow))
    for city in CITY_ORDER:
        data = by_city.get(city)
        if not data:
            continue
        rows.append(["", CITY_ALIAS_P1.get(city, city)] + _grow_cells(data, grow.get(city, {})))
    return rows


def _grow_cells(stat: dict, grow: dict) -> list[Any]:
    cells: list[Any] = []
    for g in GRADES:
        s = _build_section(stat, grow, g)
        cells += [s[k] for k in (
            "落责数量", "落责准确客户数量", "落责准确率%",
            "直销落责数量", "有客户经理服务的客户数量", "有客户经理占比%",
            "收入正增长客户数量", "正增长客户占比%",
        )]
    return cells
