"""模块② 数据分析 —— C 类：人工供数的模板生成与成品加工

对应《数据分析工作流.md》的「③ 人工供数」：
  系统无源 → 人工提供数据 + 模板 → 平台按模板生成

流程：
  ① 平台生成**空白模板** → 人工下载
  ② 人工把收集到的数据填进模板 → 放 inbox/
  ③ 平台读取 → 按成品结构生成 → outputs/

已覆盖：
  · 每周活动汇总（5 个活动 × 7 列）
  · 白皮书 3.5 潜客转化（区域 / 分公司 / 派单量...）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import tables
from ..core.config import INBOX_DIR, OUTPUT_DIR, TEMPLATE_DIR
from ..core.logger import get_logger
from ..core.rules import as_float, compute_rate
from ..core.runner import TaskResult, registry

log = get_logger("data.manual")

MODULE = "数据分析"

# 统一的白皮书输出文件（与资料包一致：政企码号台阶白皮书202608.xlsx）
# 三张表（1.6 / 2.11 / 3.5）分别由不同任务写入同一个文件的对应 sheet
OUT_WHITEPAPER = "政企码号台阶白皮书202608.xlsx"
WP_SHEET_ORDER = ["目录", "取数说明", "1.6", "2.11", "3.5"]

# 每周活动汇总的 5 个活动（案例中确认）
WEEKLY_ACTIVITIES = [
    "2026Q2泛政务存量客户全光组网营销活动第三批0930",
    "2026Q2泛政务存量客户全光组网营销活动第二批0930",
    "2026Q2酒店存量客户全光营销活动0930",
    "2026政企专线感恩提速专项营销活动-0930",
    "2026Q3存量VPN群招工扩群商机营销活动-0930【中台】",
]

# 各地市（用于模板预填行）
CITIES = [
    "省中心", "广州", "深圳", "东莞", "佛山", "中山", "惠州", "江门", "珠海",
    "揭阳", "潮州", "汕尾", "汕头", "湛江", "茂名", "阳江", "云浮", "肇庆",
    "清远", "梅州", "韶关", "河源",
]

# 3.5 潜客转化的区域分组
SUB3_5_ROWS = [
    ("全省", "全省"),
    ("珠一区", "深圳"), ("", "广州"), ("", "东莞"), ("", "佛山"),
    ("珠二区", "中山"), ("", "惠州"), ("", "江门"), ("", "珠海"),
    ("粤东", "揭阳"), ("", "潮州"), ("", "汕尾"), ("", "汕头"),
    ("粤西", "湛江"), ("", "茂名"), ("", "阳江"), ("", "云浮"),
    ("粤北", "肇庆"), ("", "清远"), ("", "梅州"), ("", "韶关"), ("", "河源"),
]


# ------------------------------------------------------------------ 模板生成
# 与 data_weekly_summary 保持一致的常量
WEEKLY_CITY_ORDER = [
    "省中心", "广州", "汕尾", "阳江", "揭阳", "茂名", "江门", "韶关",
    "惠州", "梅州", "汕头", "深圳", "珠海", "佛山", "肇庆", "湛江",
    "中山", "河源", "云浮", "潮州", "东莞", "清远",
]
WEEKLY_METRICS = ["总工单数", "派单量", "回单量", "回单率", "转化量", "转化率"]


def make_weekly_template() -> Path:
    """生成「每周活动汇总」空白模板

    结构（与案例 2-每周活动汇总-0930.xlsx 完全一致）：
      每个活动占 8 列：地市 + 6 指标 + 1 空分隔列
      5 个活动共 5*8-1 = 39 列，22 个地市 + 2 行表头 + 收尾
    """
    header1: list[Any] = []
    header2: list[Any] = []
    for act in WEEKLY_ACTIVITIES:
        header1 += ["地市", act] + [""] * 6
        header2 += [""] + WEEKLY_METRICS + [""]

    rows: list[list[Any]] = [header1, header2]
    for city in WEEKLY_CITY_ORDER:
        row: list[Any] = []
        for _ in WEEKLY_ACTIVITIES:
            row += [city] + [""] * 6 + [""]
        rows.append(row)

    rows.append([""] * len(header1))
    tail = [""] * len(header1)
    for k in range(len(WEEKLY_ACTIVITIES)):
        base = k * 8
        if base + 2 < len(tail):
            tail[base + 2] = "活动结束时间"
    rows.append(tail)

    name = "模板-每周活动汇总.xlsx"
    path = TEMPLATE_DIR / name
    tables.write_xlsx(path, {"每周活动汇总": rows})
    return path


def make_sub35_template() -> Path:
    """生成「3.5 潜客转化」空白模板"""
    rows: list[list[Any]] = [
        ["潜客转化", "", "", "", "", "", ""],
        ["区域", "分公司", "派单量", "回单量", "回单率", "转化量", "转化率"],
    ]
    for region, branch in SUB3_5_ROWS:
        rows.append([region, branch, "", "", "", "", ""])
    rows.append([])
    rows.append(["填写说明：", "① 只需填 派单量 / 回单量 / 转化量 三列"])
    rows.append(["", "② 回单率 = 回单量 / 派单量；转化率 = 转化量 / 派单量（平台自动算）"])

    name = "模板-3.5潜客转化.xlsx"
    path = TEMPLATE_DIR / name
    tables.write_xlsx(path, {"3.5": rows})
    return path


@registry.register(
    key="make_templates",
    name="生成模板（人工供数用）",
    module=MODULE,
    description="生成兼容旧流程的输出结构模板（新流程优先直接上传业务数据源）",
    source_type="auto",
    source_note="无需源数据，直接生成空白模板供人工填写。",
    outputs_desc="模板-每周活动汇总.xlsx（对应 2-每周活动汇总） / 模板-3.5潜客转化.xlsx（对应 白皮书 sheet 3.5）",
)
def make_templates() -> TaskResult:
    outs = [str(make_weekly_template()), str(make_sub35_template())]
    return TaskResult(
        ok=True,
        message=f"已生成 {len(outs)} 个空白模板（供人工供数使用）",
        outputs=outs,
        metrics={
            "每周活动汇总": f"{len(WEEKLY_ACTIVITIES)} 个活动 × 7 列 × {len(CITIES)} 地市",
            "3.5潜客转化": f"{len(SUB3_5_ROWS)} 行 × 7 列",
            "存放目录": str(TEMPLATE_DIR),
        },
    )


# ------------------------------------------------------------------ 成品加工
def _find_template(inbox: Path, keyword: str) -> Path | None:
    """在 inbox 或 templates 里找模板"""
    for d in (inbox, TEMPLATE_DIR):
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            if p.is_file() and keyword in p.name and not p.name.startswith(("~$", ".~")):
                return p
    return None


# 【已弃用】简版每周活动汇总 —— 已被 data_weekly_summary.weekly_activity_summary 取代
@registry.register(
    key="manual_sub35",
    name="3.5 潜客转化（人工供数）",
    module=MODULE,
    description="读取人工填好的潜客转化数据，自动计算回单率/转化率，生成白皮书 3.5 表",
    source_type="auto",
    source_note=(
        "【为什么是人工供数】\n"
        "  白皮书 3.5 的取数说明里，数据来源列为空 —— 系统里没有对应报表\n"
        "\n"
        "【第一步：拿模板】\n"
        "  点「生成模板（人工供数用）」，得到 模板-3.5潜客转化.xlsx\n"
        "\n"
        "【第二步：填数据】\n"
        "  只填 3 列：派单量 / 回单量 / 转化量\n"
        "\n"
        "【平台自动算的比率（标准口径）】\n"
        "  回单率 = 回单量 ÷ 派单量\n"
        "  转化率 = 转化量 ÷ 派单量\n"
        "\n"
        "【第三步：落盘】\n"
        "  填好后放 data/inbox/，文件名含「3.5」或「潜客转化」即可\n"
        "\n"
        "【平台产出】\n"
        "  政企码号台阶白皮书202608.xlsx → sheet「3.5」"
    ),
    outputs_desc="政企码号台阶白皮书202608.xlsx → sheet「3.5」潜客转化",
    required_sources=[
        {"label": "潜客转化数据（人工填报模板）", "keywords": ["3.5", "潜客转化"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def manual_sub35(inbox: str | None = None) -> TaskResult:
    inbox_path = Path(inbox) if inbox else INBOX_DIR
    src = _find_template(inbox_path, "3.5") or _find_template(inbox_path, "潜客转化")
    if src and "模板-" in src.name and not any(
        str(c).strip() for r in tables.read_any(src)[2:] for c in r[2:] if c is not None
    ):
        src = None  # 还是空模板

    if not src:
        tmpl = make_sub35_template()
        return TaskResult(
            ok=False,
            message=(
                "未找到填好的「3.5 潜客转化」数据。\n"
                f"已生成空白模板：{tmpl.name}\n"
                f"请填好后放入 {inbox_path}/，文件名含「3.5」或「潜客转化」即可。"
            ),
            outputs=[str(tmpl)],
            metrics={"模板路径": str(tmpl)},
        )

    rows = tables.read_any(src)
    rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
    if len(rows) < 3:
        return TaskResult(ok=False, message="文件内容太少")

    warnings: list[str] = []
    out_rows: list[list[Any]] = []
    header_done = False
    filled = 0

    for r in rows:
        row = list(r)
        while len(row) < 7:
            row.append("")
        if str(row[0]).startswith(("填写说明", "①", "②")):
            continue
        if str(row[0]).strip() == "潜客转化" or str(row[0]).strip() == "区域":
            out_rows.append(row)
            if str(row[0]).strip() == "区域":
                header_done = True
            continue
        if str(row[0]).strip() == "" and str(row[1]).strip() == "":
            continue

        # 算率
        dispatch = as_float(row[2])
        received = as_float(row[3])
        converted = as_float(row[5])
        row[4] = compute_rate(received, dispatch, 4) or ""
        row[6] = compute_rate(converted, dispatch, 4) or ""
        if dispatch is not None:
            filled += 1
        out_rows.append(row)

    if not header_done:
        warnings.append("未识别到标准表头，已按原样输出")

    out = tables.upsert_sheet(OUTPUT_DIR / OUT_WHITEPAPER, "3.5", out_rows, WP_SHEET_ORDER)

    return TaskResult(
        ok=True,
        message=f"生成完成：{filled} 行有数据",
        outputs=[str(out)],
        metrics={
            "源文件": src.name,
            "有数据行": filled,
            "回单率/转化率": "已自动计算",
        },
        warnings=warnings,
    )
