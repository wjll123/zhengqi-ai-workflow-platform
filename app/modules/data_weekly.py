"""模块② 数据分析 —— 周报加工

对应《源数据映射矩阵.md》§1 的四张表加工：
  T1 841营销活动管控表     ← 841营销活动管控表样例.xlsx  （33列全同，列一一对应）
  T2 智慧中台调度工单查询   ← 智慧中台调度工单查询样例.csv （28列全同，GBK）
  T3 智慧中台活动评估       ← 智慧中台活动评估样例.xls    （BIFF，直接搬运 23×22）
  T4 智慧中台接触工单查询   ← 智慧中台接触工单查询样例.xlsx（21列 + 派生 `地市抽取`）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import tables
from ..core.logger import get_logger
from ..core.rules import (
    CITY_ORDER_OVERRIDE,
    compute_rate,
    extract_city,
    sort_rows_by_city,
)
from ..core.runner import TaskResult, registry

log = get_logger("data.weekly")

# 统一的周报输出文件（与资料包一致：1-接单情况模板.xlsx）
OUT_WEEKLY = "1-接单情况模板.xlsx"
SHEET_ORDER = [
    "营销活动数据统计",
    "841营销活动管控表",
    "智慧中台活动评估",
    "智慧中台调度工单查询",
    "智慧中台接触工单查询",
]

MODULE = "数据分析"


def _find(inbox: Path, *keywords: str) -> Path | None:
    """在收件目录里按关键词找文件"""
    if not inbox.exists():
        return None
    files = [p for p in inbox.iterdir() if p.is_file() and not p.name.startswith(("~$", ".~"))]
    for kw in keywords:
        for p in files:
            if kw in p.name:
                return p
    return None


def _skip_placeholder(row: list[Any]) -> bool:
    """跳过空行"""
    return not any(str(c).strip() for c in row if c is not None)


# ---------------------------------------------------------------- T1 841 表
@registry.register(
    key="weekly_841",
    name="841营销活动管控表加工",
    module=MODULE,
    description="读取 841 管控表源文件，按列一一对应输出（含市→区县→营服三级）",
    source_type="auto",
    source_note="CDAP 导出 841 营销活动管控表",
    outputs_desc="1-接单情况模板.xlsx → sheet「841营销活动管控表」",
    required_sources=[
        {"label": "841营销活动管控表（CDAP 导出）", "keywords": ["841", "营销活动管控"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def weekly_841(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    src = _find(inbox_path, "841", "营销活动管控")
    if not src:
        return TaskResult(ok=False, message="未找到 841 营销活动管控表源文件")

    log.info("读取源文件: %s", src.name)
    rows = tables.read_any(src)
    rows = [r for r in rows if not _skip_placeholder(r)]

    if not rows:
        return TaskResult(ok=False, message="源文件为空")

    header = rows[0]
    out = tables.upsert_sheet(
        OUTPUT_DIR / OUT_WEEKLY, "841营销活动管控表", rows, SHEET_ORDER
    )

    body = rows[1:]
    lv1 = sum(1 for r in body if str(r[0]).strip() and not str(r[1] if len(r) > 1 else "").strip())
    lv2 = sum(1 for r in body if len(r) > 1 and str(r[1]).strip() and not str(r[2] if len(r) > 2 else "").strip())
    lv3 = len(body) - lv1 - lv2

    return TaskResult(
        ok=True,
        message=f"加工完成：{len(body)} 行 × {len(header)} 列",
        outputs=[str(out)],
        metrics={
            "源文件": src.name,
            "数据行": len(body),
            "列数": len(header),
            "层级": {"市": lv1, "区县": lv2, "营服": lv3},
        },
    )


# ------------------------------------------------------- T2 调度工单查询（GBK）
@registry.register(
    key="weekly_dispatch_ticket",
    name="智慧中台调度工单查询加工",
    module=MODULE,
    description="读取 GBK 编码的调度工单 CSV，列一一对应输出（28 列）",
    source_type="auto",
    source_note="智慧中台导出「调度工单查询」，导出为 CSV（GBK 编码）",
    outputs_desc="1-接单情况模板.xlsx → sheet「智慧中台调度工单查询」",
    required_sources=[
        {"label": "智慧中台调度工单查询（CSV/GBK 导出）", "keywords": ["调度工单"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def weekly_dispatch_ticket(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    src = _find(inbox_path, "调度工单")
    if not src:
        return TaskResult(ok=False, message="未找到 调度工单查询 源文件")

    log.info("读取源文件（GBK）: %s", src.name)
    rows = tables.read_csv_gbk(src)
    rows = [r for r in rows if not _skip_placeholder(r)]
    header = rows[0]

    out = tables.upsert_sheet(
        OUTPUT_DIR / OUT_WEEKLY, "智慧中台调度工单查询", rows, SHEET_ORDER
    )

    status_idx = header.index("工单状态") if "工单状态" in header else None
    status_count: dict[str, int] = {}
    if status_idx is not None:
        for r in rows[1:]:
            if len(r) > status_idx:
                k = str(r[status_idx]).strip() or "空"
                status_count[k] = status_count.get(k, 0) + 1

    return TaskResult(
        ok=True,
        message=f"加工完成：{len(rows) - 1} 行 × {len(header)} 列",
        outputs=[str(out)],
        metrics={
            "源文件": src.name,
            "编码": "GBK",
            "数据行": len(rows) - 1,
            "列数": len(header),
            "工单状态分布": dict(sorted(status_count.items(), key=lambda x: -x[1])[:8]),
        },
    )


# ------------------------------------------------- T3 活动评估（BIFF .xls）
@registry.register(
    key="weekly_activity_eval",
    name="智慧中台活动评估搬运",
    module=MODULE,
    description="读取 BIFF 格式 .xls（需 xlrd），原样搬运（源 23×22 = 输出 23×22）",
    source_type="auto",
    source_note="智慧中台导出「活动评估」（BIFF 格式 .xls，WPS 保存）",
    outputs_desc="1-接单情况模板.xlsx → sheet「智慧中台活动评估」",
    required_sources=[
        {"label": "智慧中台活动评估（.xls 导出）", "keywords": ["活动评估"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def weekly_activity_eval(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    src = _find(inbox_path, "活动评估")
    if not src:
        return TaskResult(ok=False, message="未找到 活动评估 源文件")

    log.info("读取 BIFF .xls: %s", src.name)
    rows = tables.read_xls_biff(src)
    rows = [r for r in rows if not _skip_placeholder(r)]

    out = tables.upsert_sheet(
        OUTPUT_DIR / OUT_WEEKLY, "智慧中台活动评估", rows, SHEET_ORDER
    )

    return TaskResult(
        ok=True,
        message=f"搬运完成：{len(rows)} 行（含 2 行复合表头）",
        outputs=[str(out)],
        metrics={"源文件": src.name, "格式": "BIFF .xls", "读取器": "xlrd", "行数": len(rows)},
    )


# ------------------------------- T4 接触工单（派生 `地市抽取` 列）
@registry.register(
    key="weekly_contact_ticket",
    name="智慧中台接触工单查询加工",
    module=MODULE,
    description="读取接触工单，末列由「对象标识」替换为派生列「地市抽取」（从活动名称提取）",
    source_type="auto",
    source_note="智慧中台导出「接触工单查询」",
    outputs_desc="1-接单情况模板.xlsx → sheet「智慧中台接触工单查询」（含派生列 地市抽取）",
    required_sources=[
        {"label": "智慧中台接触工单查询（导出）", "keywords": ["接触工单"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def weekly_contact_ticket(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    src = _find(inbox_path, "接触工单")
    if not src:
        return TaskResult(ok=False, message="未找到 接触工单查询 源文件")

    log.info("读取源文件: %s", src.name)
    rows = tables.read_xlsx(src, "Sheet1")
    rows = [r for r in rows if not _skip_placeholder(r)]
    header = rows[0]

    if "活动名称" not in header:
        return TaskResult(ok=False, message="源文件缺少「活动名称」列，无法派生地市")

    act_idx = header.index("活动名称")

    # 输出表头：末列换成「地市抽取」
    out_header = list(header[:-1]) + ["地市抽取"]

    warnings: list[str] = []
    body: list[list[Any]] = []
    matched = 0
    for r in rows[1:]:
        row = list(r[: len(header) - 1])
        while len(row) < len(header) - 1:
            row.append("")
        act = r[act_idx] if len(r) > act_idx else ""
        city = extract_city(act)
        if city:
            matched += 1
        row.append(city)
        body.append(row)

    total = len(body)
    if total and matched / total < 0.95:
        warnings.append(f"地市抽取匹配率偏低：{matched}/{total}，请检查活动名称格式")

    out = tables.upsert_sheet(
        OUTPUT_DIR / OUT_WEEKLY, "智慧中台接触工单查询", [out_header] + body, SHEET_ORDER
    )

    city_count: dict[str, int] = {}
    for r in body:
        city_count[r[-1]] = city_count.get(r[-1], 0) + 1

    return TaskResult(
        ok=True,
        message=f"加工完成：{total} 行，地市抽取匹配 {matched}/{total}",
        outputs=[str(out)],
        metrics={
            "源文件": src.name,
            "数据行": total,
            "列数": len(out_header),
            "派生列": "地市抽取（源「对象标识」已丢弃）",
            "匹配率": f"{matched}/{total}",
            "地市分布": dict(sorted(city_count.items(), key=lambda x: -x[1])),
        },
        warnings=warnings,
    )


# ------------------------------------------- 组合任务：一次跑完四张表
@registry.register(
    key="weekly_all",
    name="周报加工（四张表一键）",
    module=MODULE,
    description="按顺序执行 841表 → 调度工单 → 活动评估 → 接触工单，汇总结果",
    source_type="auto",
    source_note="按顺序执行上述四张表的源文件",
    outputs_desc="1-接单情况模板.xlsx（4 张子表：841 / 调度工单 / 活动评估 / 接触工单）",
    required_sources=[
        {"label": "841营销活动管控表", "keywords": ["841", "营销活动管控"], "batch_key": True},
        {"label": "智慧中台调度工单查询", "keywords": ["调度工单"], "batch_key": True},
        {"label": "智慧中台活动评估", "keywords": ["活动评估"], "batch_key": True},
        {"label": "智慧中台接触工单查询", "keywords": ["接触工单"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def weekly_all(inbox: str | None = None) -> TaskResult:
    from ..core.config import OUTPUT_DIR

    # 先清空旧输出，确保 4 张子表干净重建（避免上次残留的 sheet）
    target = OUTPUT_DIR / OUT_WEEKLY
    if target.exists():
        try:
            target.unlink()
        except Exception:
            pass

    steps = [
        ("841 表", weekly_841),
        ("调度工单", weekly_dispatch_ticket),
        ("活动评估", weekly_activity_eval),
        ("接触工单", weekly_contact_ticket),
    ]
    outputs: list[str] = []
    metrics: dict[str, Any] = {}
    warnings: list[str] = []
    failed: list[str] = []

    for label, fn in steps:
        res = fn(inbox) if inbox else fn()
        if res.ok:
            outputs.extend(res.outputs)
            metrics[label] = res.metrics
            warnings.extend([f"{label}: {w}" for w in res.warnings])
        else:
            failed.append(f"{label}: {res.message}")

    ok = not failed
    msg = f"完成 {len(outputs)}/4 张表" + (f"；失败：{'; '.join(failed)}" if failed else "")
    return TaskResult(ok=ok, message=msg, outputs=outputs, metrics=metrics, warnings=warnings)
