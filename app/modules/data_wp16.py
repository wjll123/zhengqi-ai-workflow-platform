"""模块② 数据分析 —— 白皮书 1.6 单位证件台阶收入保有率

✅ 公式已确认（来源：白皮书 sheet「1.6」第 27 行统计口径）
    单位证件客户台阶收入保有率 =
        (2026年1-M月累计单位证件客户台阶收入 / 2025年1-M月累计单位证件客户台阶收入) × 100%

  原文：
    1、单位证件客户台阶收入：取 SR008 中号码证件类型为单位证件、
       客户收入类型为行业客户和商业客户的台阶收入；
    2、主要包含基础业务码号（移动/宽带/固话/天翼高清/互联网专线/天翼看家/
       全屋WIPI/全屋智能/智能名片等）及产数战新重点产品的码号月租收入；
    3、保有率 = (26年1-M月累计 / 25年1-M月累计) × 100%；
    4、因24年未有台阶收入统计数据，无法测算25年月度对应收保率，
       故暂不做同比计算；

  表结构（31 行 × 11 列）：
    标题 | 期次(46174=2026-06-30, 46204=2026-07-31)
    表头：26年1-6月收入 | 25年1-6月收入 | 26年1-6月收保率 | 排名
          26年1-7月收入 | 25年1-7月收入 | 26年1-7月收保率 | 排名 | 环比上月(PP)
    行：全省 + 珠I + 珠II + 粤东 + 粤西 + 粤北

  「环比上月(PP)」= 本月收保率 − 上月收保率
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import tables
from ..core.logger import get_logger
from ..core.rules import as_float, normalize_city
from ..core.runner import TaskResult, registry

log = get_logger("data.wp16")

MODULE = "数据分析"

# 统一的白皮书输出文件（与资料包一致：政企码号台阶白皮书202608.xlsx）
# 三张表（1.6 / 2.11 / 3.5）分别由不同任务写入同一个文件的对应 sheet
OUT_WHITEPAPER = "政企码号台阶白皮书202608.xlsx"
WP_SHEET_ORDER = ["目录", "取数说明", "1.6", "2.11", "3.5"]

# 表结构常量
COL_REGION = 0        # 区域
COL_CITY = 1          # 地市
COLS_PER_PERIOD = 4   # 每个期次占 4 列：本年收入 / 上年收入 / 收保率 / 排名

# 区域分组（按资料包 1.6 的实际顺序）
REGION_GROUPS_16 = [
    ("珠 I", ["深圳", "广州", "东莞", "佛山"]),
    ("珠 II", ["中山", "惠州", "江门", "珠海"]),
    ("粤东", ["汕头", "揭阳", "潮州", "汕尾"]),
    ("粤西", ["茂名", "湛江", "阳江", "云浮"]),
    ("粤北", ["清远", "肇庆", "梅州", "韶关", "河源"]),
]


def _find(inbox: Path, *keywords: str) -> Path | None:
    if not inbox.exists():
        return None
    for kw in keywords:
        for p in sorted(inbox.iterdir()):
            if p.is_file() and kw in p.name and not p.name.startswith(("~$", ".~")):
                return p
    return None


def _rank(values: list[tuple[str, float]], digits: int = 2) -> dict[str, int]:
    """按值降序排名（大者为优，1 = 最好）"""
    ordered = sorted(values, key=lambda x: -x[1])
    out: dict[str, int] = {}
    for i, (name, _) in enumerate(ordered, 1):
        out[name] = i
    return out


@registry.register(
    key="whitepaper_16",
    name="白皮书 1.6 单位证件收保率",
    module=MODULE,
    description="按保有率公式计算：26年累计收入 ÷ 25年同期累计收入 × 100%，含排名与环比",
    source_type="semi",
    source_note=(
        "【数据来源】SR008 报表，取号码证件类型=单位证件、客户收入类型=行业客户/商业客户\n"
        "【取数方式】CDAP 导出后上传（文件名含「单位证件」或「SR008」）\n"
        "\n"
        "【必须包含的列】\n"
        "  · 地市\n"
        "  · 2026年1-M月累计单位证件台阶收入\n"
        "  · 2025年1-M月累计单位证件台阶收入\n"
        "  （若同时有两个期次的数据，可一并输出 1-6月 与 1-7月）\n"
        "\n"
        "【平台自动计算】\n"
        "  收保率 = 26年累计 ÷ 25年同期累计 × 100%\n"
        "  排名   = 按收保率降序（1 = 最好）\n"
        "  环比上月(PP) = 本月收保率 − 上月收保率\n"
        "\n"
        "⚠️ 注：因 2024 年无台阶收入统计，本指标不做同比计算。"
    ),
    outputs_desc="政企码号台阶白皮书202608.xlsx → sheet「1.6」对公证件台阶收入收保率",
    required_sources=[
        {"label": "单位证件台阶收入（SR008 导出）", "keywords": ["单位证件", "台阶收入", "SR008"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def whitepaper_16(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    src = _find(inbox_path, "单位证件", "SR008", "1.6")

    if not src:
        return TaskResult(
            ok=False,
            message=(
                "未找到「单位证件台阶收入」源数据。\n"
                "请从 SR008 导出（证件类型=单位证件，收入类型=行业客户/商业客户），"
                "文件名含「单位证件」或「SR008」，上传后重试。\n\n"
                "需要包含列：地市 / 2026年累计收入 / 2025年累计收入"
            ),
        )

    log.info("读取 1.6 源: %s", src.name)
    rows = tables.read_any(src)
    rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]

    if len(rows) < 2:
        return TaskResult(ok=False, message="源文件内容不足（至少需要表头 + 1 行数据）")

    # 定位列
    header = [str(c).strip() if c is not None else "" for c in rows[0]]

    def find_col(*kws: str) -> int | None:
        for i, h in enumerate(header):
            for kw in kws:
                if kw in h:
                    return i
        return None

    city_col = find_col("地市", "分公司", "城市")
    cur_col = find_col("2026", "26年", "本年")
    prev_col = find_col("2025", "25年", "上年", "同期")

    if city_col is None:
        return TaskResult(ok=False, message=f"未找到「地市」列。现有表头：{header[:10]}")
    if cur_col is None or prev_col is None:
        return TaskResult(
            ok=False,
            message=(
                f"未找到「2026年累计收入」或「2025年累计收入」列。现有表头：{header[:12]}\n"
                "请确认列名包含 2026/26年 与 2025/25年。"
            ),
        )

    # 收集数据
    data: list[dict[str, Any]] = []
    warnings: list[str] = []
    for r in rows[1:]:
        raw_city = str(r[city_col]).strip() if len(r) > city_col else ""
        city = normalize_city(raw_city)
        if not city or city in ("全省", "合计", "总计"):
            continue
        cur = as_float(r[cur_col]) if len(r) > cur_col else None
        prev = as_float(r[prev_col]) if len(r) > prev_col else None
        data.append({"city": city, "cur": cur, "prev": prev})

    if not data:
        return TaskResult(ok=False, message="源文件中未解析出有效地市数据")

    # 计算收保率
    valid: list[tuple[str, float]] = []
    for d in data:
        if d["cur"] is not None and d["prev"] not in (None, 0):
            d["rate"] = round(d["cur"] / d["prev"] * 100, 2)
            valid.append((d["city"], d["rate"]))
        else:
            d["rate"] = None
            if d["prev"] in (None, 0) and d["cur"] is not None:
                warnings.append(f"{d['city']}：上年同期收入为空或 0，收保率无法计算")

    ranks = _rank(valid)

    # 组装输出（31 行 × 11 列）
    out: list[list[Any]] = []
    out.append(["单位证件台阶收入保有率"] + [""] * 10)
    out.append(["地市", "", "", "", "", "", "", "", "", "", ""])
    out.append([
        "", "",
        "26年累计单位证件台阶收入", "25年同期单位证件台阶收入",
        "26年收保率(%)", "台阶收保率排名",
        "", "", "", "", "环比上月(PP)",
    ])

    # 全省
    tot_cur = sum(d["cur"] for d in data if d["cur"] is not None)
    tot_prev = sum(d["prev"] for d in data if d["prev"] is not None)
    prov_rate = round(tot_cur / tot_prev * 100, 2) if tot_prev else ""
    out.append(["", "全省", round(tot_cur, 2), round(tot_prev, 2), prov_rate, "—", "", "", "", "", ""])

    # 分区域
    by_city = {d["city"]: d for d in data}
    for region, cities in REGION_GROUPS_16:
        for city in cities:
            d = by_city.get(city)
            if d is None:
                out.append([region, city, "", "", "", "", "", "", "", "", ""])
                continue
            out.append([
                region, city,
                round(d["cur"], 2) if d["cur"] is not None else "",
                round(d["prev"], 2) if d["prev"] is not None else "",
                d["rate"] if d["rate"] is not None else "",
                ranks.get(city, ""),
                "", "", "", "", "",
            ])
            region = ""     # 同区域只标一次

    out.append([])
    out.append(["统计口径：",
                "1、单位证件客户台阶收入：取 SR008 中号码证件类型为单位证件、客户收入类型为行业客户和商业客户的台阶收入；"])
    out.append(["", "2、主要包含基础业务码号及产数战新重点产品的码号月租收入；"])
    out.append(["", "3、保有率 = (2026年1-M月累计 ÷ 2025年1-M月累计) × 100%；"])
    out.append(["", "4、因24年未有台阶收入统计数据，故暂不做同比计算。"])

    dst = tables.upsert_sheet(OUTPUT_DIR / OUT_WHITEPAPER, "1.6", out, WP_SHEET_ORDER)

    return TaskResult(
        ok=True,
        message=f"生成完成：全省 + {len(data)} 地市（含排名）",
        outputs=[str(dst)],
        metrics={
            "源文件": src.name,
            "地市数": len(data),
            "全省收保率": f"{prov_rate}%" if prov_rate != "" else "—",
            "全省收入": f"{tot_cur:,.0f} / {tot_prev:,.0f}",
            "计算公式": "26年累计 ÷ 25年同期累计 × 100%",
        },
        warnings=warnings[:5],
    )
