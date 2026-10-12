"""模块② 数据分析 —— 每周活动汇总

输出结构（实测自案例 2-每周活动汇总-0930.xlsx）：
  Sheet1「每周活动汇总」：5 个活动 × 7 列（地市 + 6 指标）
  每 7 列一块：地市 | 总工单数 | 派单量 | 回单量 | 回单率 | 转化量 | 转化率

  地市顺序（固定，22 行）：
    省中心 / 广州 / 汕尾 / 阳江 / 揭阳 / 茂名 / 江门 / 韶关 / 惠州 / 梅州
    / 汕头 / 深圳 / 珠海 / 佛山 / 肇庆 / 湛江 / 中山 / 河源 / 云浮 / 潮州
    / 东莞 / 清远

5 个活动（固定顺序）：
  1. 2026Q2泛政务存量客户全光组网营销活动第三批0930
  2. 2026Q2泛政务存量客户全光组网营销活动第二批0930
  3. 2026Q2酒店存量客户全光营销活动0930
  4. 2026政企专线感恩提速专项营销活动-0930
  5. 2026Q3存量VPN群招工扩群商机营销活动-0930【中台】
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import tables
from ..core.logger import get_logger
from ..core.rules import activity_rate, as_float, compute_rate, normalize_city
from ..core.runner import TaskResult, registry

log = get_logger("data.weekly_sum")

MODULE = "数据分析"

# 固定地市顺序（22 行，实测自案例）
CITY_ORDER = [
    "省中心", "广州", "汕尾", "阳江", "揭阳", "茂名", "江门", "韶关",
    "惠州", "梅州", "汕头", "深圳", "珠海", "佛山", "肇庆", "湛江",
    "中山", "河源", "云浮", "潮州", "东莞", "清远",
]

# 5 个活动（固定顺序）
ACTIVITIES = [
    "2026Q2泛政务存量客户全光组网营销活动第三批0930",
    "2026Q2泛政务存量客户全光组网营销活动第二批0930",
    "2026Q2酒店存量客户全光营销活动0930",
    "2026政企专线感恩提速专项营销活动-0930",
    "2026Q3存量VPN群招工扩群商机营销活动-0930【中台】",
]

METRICS = ["总工单数", "派单量", "回单量", "回单率", "转化量", "转化率"]

METRIC_ALIASES = {
    "总工单数": ("总工单数", "工单总数", "总工单量", "工单数", "工单量"),
    "派单量": ("派单量", "派单数", "下发量", "下发数", "有效派单量"),
    "回单量": ("回单量", "回单数", "回收量", "回收数", "回收工单量", "批量回收工单量", "已回单数"),
    "回单率": ("回单率", "回收率", "回单率(%)", "回收率(%)"),
    "转化量": ("转化量", "转化数", "营销成功数", "成交量", "转化总数"),
    "转化率": ("转化率", "转化率(%)", "营销成功率", "营销成功率(%)"),
}


def _clean_label(value: Any) -> str:
    return "".join(str(value or "").strip().lower().split()).replace("（", "(").replace("）", ")")


def _metric_label(value: Any) -> str | None:
    label = _clean_label(value)
    for metric, aliases in METRIC_ALIASES.items():
        if label in {_clean_label(x) for x in aliases}:
            return metric
    return None


def _activity_key(value: Any) -> str:
    """把日期、批次和中台后缀差异去掉，用包含关系匹配目标活动。"""
    s = _clean_label(value)
    for token in ("【中台】", "中台", "活动", "营销活动"):
        s = s.replace(_clean_label(token), "")
    import re
    s = re.sub(r"20\d{2}[-_/年]?\d{0,2}[-_/月]?\d{0,2}", "", s)
    s = re.sub(r"q[1-4]", "", s)
    s = re.sub(r"[^a-z0-9\u4e00-\u9fa5]", "", s)
    return s


def _canonical_activity(value: Any) -> str:
    raw = str(value or "").strip()
    key = _activity_key(raw)
    # 业务常见简称：源系统经常省略年份、泛政务、批次日期或“营销活动”字样。
    if "全光组网" in raw and "第三批" in raw:
        return ACTIVITIES[0]
    if "全光组网" in raw and "第二批" in raw:
        return ACTIVITIES[1]
    if "酒店" in raw and "全光" in raw:
        return ACTIVITIES[2]
    if "专线" in raw and "提速" in raw:
        return ACTIVITIES[3]
    if "vpn" in raw.lower() and ("群招" in raw or "扩群" in raw):
        return ACTIVITIES[4]
    # 先精确，再按去掉日期/批次后的包含关系匹配。
    for target in ACTIVITIES:
        target_key = _activity_key(target)
        if key == target_key or key in target_key or target_key in key:
            return target
    return raw

# ⚠️ 实测发现：源数据的「回单率/转化率」口径不统一
#   · 泛政务第三批/第二批/酒店全光 → 分母=派单量
#   · 政企专线感恩 → 混合（部分用总工单数）
#   · VPN群招 → 分母=总工单数
# 这是人工做表时的口径差异，**平台不做统一，原样保留源值**；
# 仅在源值为空时才按 分子÷派单量 兜底计算。
COLS_PER_BLOCK = 8      # 地市 + 6 指标 + 1 空列（块间分隔）
# 实测案例：块起点为 0, 8, 16, 24, 32；5 块共 5*8-1 = 39 列


def _read_matrix(src: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """读「宽表」源（每 7 列一个活动块），返回
    {活动名: {地市: {指标: 值}}}
    """
    rows = tables.read_any(src)
    rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
    if len(rows) < 3:
        return {}

    header1, header2 = rows[0], rows[1]

    # 识别活动块：表头1 中「地市」的位置 = 块起点
    # 支持两种布局：块间隔 7 列（紧凑）或 8 列（块间含 1 空列）
    blocks: list[tuple[int, str]] = []
    for i, h in enumerate(header1):
        if str(h).strip() == "地市":
            # 仅接受包含本任务指标的块，避免把其他“地市”报表误识别为源。
            metric_headers = [_metric_label(x) for x in header2[i + 1:i + 7]]
            if not all(m in metric_headers for m in METRICS):
                continue
            act = ""
            if i + 1 < len(header1) and header1[i + 1] is not None:
                act = _canonical_activity(header1[i + 1])
            if not act:
                act = f"活动{len(blocks) + 1}"
            blocks.append((i, act))

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for start, act in blocks:
        table: dict[str, dict[str, Any]] = {}
        for r in rows[2:]:
            city = normalize_city(r[start] if len(r) > start else "")
            if not city or city == "None":
                continue
            rec: dict[str, Any] = {}
            for k, metric in enumerate(metric_headers, start=1):
                idx = start + k
                if metric:
                    rec[metric] = r[idx] if len(r) > idx else ""
            table[city] = rec
        out[act] = table
    return out


def _read_long(src: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """读「长表」源（每行一条：活动名 + 地市 + 指标），返回同结构"""
    rows = tables.read_any(src)
    rows = [r for r in rows if any(str(c).strip() for c in r if c is not None)]
    if len(rows) < 2:
        return {}

    header = [str(c).strip() if c is not None else "" for c in rows[0]]

    def col(*kws: str) -> int | None:
        for i, h in enumerate(header):
            for kw in kws:
                if _clean_label(kw) in _clean_label(h):
                    return i
        return None

    act_i = col("活动名称", "活动名", "营销活动")
    city_i = col("地市", "分公司", "归属地市")
    if act_i is None or city_i is None:
        return {}

    metric_idx: dict[str, int] = {}
    for m in METRICS:
        c = None
        for alias in METRIC_ALIASES[m]:
            c = col(alias)
            if c is not None:
                break
        if c is not None:
            metric_idx[m] = c
    if not {"总工单数", "派单量", "回单量", "转化量"}.issubset(metric_idx):
        return {}

    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in rows[1:]:
        act = _canonical_activity(r[act_i] if len(r) > act_i else "")
        city = normalize_city(r[city_i] if len(r) > city_i else "")
        if not act or not city:
            continue
        rec = {m: (r[c] if len(r) > c else "") for m, c in metric_idx.items()}
        out.setdefault(act, {})[city] = rec
    return out


@registry.register(
    key="weekly_activity_summary",
    name="每周活动汇总",
    module=MODULE,
    description="识别业务数据源并汇总生成 5 个活动 × 22 地市的周报表",
    source_type="auto",
    source_note=(
        "上传业务数据源即可，平台按内容识别，不依赖文件名或人工填报模板。\n"
        "宽表需包含：地市、活动名称，以及总工单数/派单量/回单量/回单率/转化量/转化率；\n"
        "长表需包含：活动名称、地市、总工单数、派单量、回单量、转化量。\n"
        "平台按源值保留已有比率，缺少比率时自动计算，并套用固定周报输出格式。\n"
        "\n"
        "【平台产出】\n"
        "  周报-每周活动汇总.xlsx\n"
        "   · Sheet1：5 个活动 × 22 地市\n"
        "   · 历史归档：另 2 个归档活动（若源中含）"
    ),
    outputs_desc="2-每周活动汇总.xlsx → sheet「Sheet1」+「历史归档」（5 个活动 × 22 地市）",
    # 不按文件名预检：上传文件可能被业务人员重命名，实际按表头和数据识别。
    required_sources=[],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def weekly_activity_summary(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR

    # 按内容识别候选文件，不要求文件名含“每周活动汇总”。
    # 这样业务人员把源文件改成任意名称，只要表头和指标齐全仍可处理。
    src = None
    candidates: list[Path] = []
    for d in (inbox_path,):
        if not d.exists():
            continue
        for p in sorted(d.iterdir()):
            n = p.name
            if not p.is_file() or n.startswith(("~$", ".~", ".")):
                continue
            if p.suffix.lower() in (".xlsx", ".xls", ".csv") and not n.startswith("模板-"):
                candidates.append(p)

    matrix = {}
    for candidate in candidates:
        try:
            matrix = _read_matrix(candidate)
            if not matrix:
                matrix = _read_long(candidate)
            # 至少命中一个活动块且有一个数值，才认为是本任务数据源。
            has_value = any(not _blank(rec.get("派单量"))
                            for table in matrix.values() for rec in table.values())
            if matrix and has_value and all(
                act in matrix and any(
                    not _blank(matrix[act].get(city, {}).get("派单量"))
                    for city in CITY_ORDER
                ) for act in ACTIVITIES
            ):
                src = candidate
                break
        except Exception:
            continue

    if src is None:
        return TaskResult(
            ok=False,
            message=(
                "未找到可识别的每周活动汇总数据源。\n"
                "请上传包含目标活动名称、地市，以及总工单数/派单量/回单量/转化量等字段的表格；"
                "文件名无需符合固定规范。"
            ),
            metrics={"状态": "待提供数据"},
        )

    log.info("读取每周活动汇总源: %s", src.name)

    # 候选扫描阶段已完成宽表/长表识别；这里再次读取以保持单独传入目录时的清晰性。
    matrix = _read_matrix(src) or _read_long(src)
    if not matrix:
        return TaskResult(
            ok=False,
            message=(
                f"「{src.name}」解析失败：未识别到活动数据。\n"
                "请确认表头含「地市」+ 各活动名（宽表），或含「活动名称」「地市」列（长表），"
                "并提供总工单数、派单量、回单量、转化量中的数据。"
            ),
        )

    # 空表检测：所有指标都空 → 提示去填
    filled = 0
    for act_table in matrix.values():
        for rec in act_table.values():
            for m in ("派单量", "回单量", "转化量", "总工单数", "回单率", "转化率"):
                if not _blank(rec.get(m)):
                    filled += 1
    if filled == 0:
        return TaskResult(
            ok=False,
            message=(
                f"「{src.name}」是空白模板（所有指标列为空），未生成成品。\n"
                "请先填写 总工单数/派单量/回单量/转化量（比率平台自动算）。"
            ),
            metrics={"源文件": src.name, "状态": "空模板，待填写"},
        )

    # 组装输出：以模板的 5 个活动为准（源里额外的进「历史归档」）
    out_header1: list[Any] = []
    out_header2: list[Any] = []
    for k, act in enumerate(ACTIVITIES):
        out_header1 += ["地市", act] + [""] * 6      # 地市 + 活动名 + 5 空（凑 7）+ 分隔
        out_header2 += [""] + METRICS + [""]         # 6 指标 + 分隔

    out_rows: list[list[Any]] = [out_header1, out_header2]
    for city in CITY_ORDER:
        row: list[Any] = []
        for act in ACTIVITIES:
            rec = matrix.get(act, {}).get(city, {})
            row.append(city)
            for m in METRICS:
                v = rec.get(m, "")
                if m in ("回单率", "转化率"):
                    # 标准口径：率 = 分子 ÷ 派单量（源有值则原样保留）
                    v = activity_rate(m, rec)
                row.append(v)
            row.append("")        # 块间分隔列
        out_rows.append(row)

    # 收尾两行（与案例一致）：空行 + 每个活动块的「活动结束时间」行
    out_rows.append([""] * len(out_header1))
    tail = [""] * len(out_header1)
    ends = _activity_end_dates(src)      # {活动名: 日期}
    for k, act in enumerate(ACTIVITIES):
        base = k * 8
        if base + 6 >= len(tail):
            break
        d = ends.get(act) or ends.get("默认")
        if not d:
            continue
        tail[base + 2] = "活动结束时间"
        tail[base + 4] = d
        tail[base + 6] = 1
    out_rows.append(tail)

    sheets: dict[str, list[list[Any]]] = {"每周活动汇总": out_rows}

    # 历史归档：源里除 5 个主活动外的
    archived = [a for a in matrix if a not in ACTIVITIES]
    if archived:
        ah1: list[Any] = []
        ah2: list[Any] = []
        for act in archived:
            ah1 += ["地市", act] + [""] * 6
            ah2 += [""] + METRICS + [""]
        arows: list[list[Any]] = [ah1, ah2]
        for city in CITY_ORDER:
            row = []
            for act in archived:
                rec = matrix.get(act, {}).get(city, {})
                row.append(city)
                for m in METRICS:
                    v = rec.get(m, "")
                    if m in ("回单率", "转化率"):
                        v = activity_rate(m, rec)
                    row.append(v)
                row.append("")
            arows.append(row)
        sheets["历史归档"] = arows

    dst = tables.write_xlsx(
        OUTPUT_DIR / "周报-每周活动汇总.xlsx", sheets,
        number_formats={
            ("每周活动汇总", s0 + 4): "0.0000"
            for s0 in range(0, len(out_header1), 8)
        },
    )

    # 统计
    act_stats: dict[str, int] = {}
    for act in ACTIVITIES:
        n = sum(
            1 for city in CITY_ORDER
            if any(not _blank(matrix.get(act, {}).get(city, {}).get(m))
                   for m in ("派单量", "回单量", "转化量"))
        )
        act_stats[act[:24]] = n

    warnings: list[str] = []
    empty_acts = [a for a, n in act_stats.items() if n == 0]
    if empty_acts:
        warnings.append(f"以下活动无数据：{'、'.join(empty_acts)}")

    return TaskResult(
        ok=True,
        message=(
            f"生成完成：{len(ACTIVITIES)} 个活动 × {len(CITY_ORDER)} 地市"
            + (f" + 历史归档 {len(archived)} 个活动" if archived else "")
        ),
        outputs=[str(dst)],
        metrics={
            "源文件": src.name,
            "活动数": len(ACTIVITIES),
            "地市行数": len(CITY_ORDER),
            "有数据活动": sum(1 for n in act_stats.values() if n > 0),
            "历史归档": len(archived),
            "比率": "回单率/转化率已自动计算",
        },
        warnings=warnings,
    )


def _activity_end_dates(src: Path) -> dict[str, str]:
    """从源文件收尾行提取各活动的「活动结束时间」

    案例格式（第 26 行）：
      块0: 列2=活动结束时间 列4=日期 列6=1
      块1: 列10=活动结束时间 列12=日期 列14=1
      ...
    返回按块序号 → {块 k: 日期}，再映射到活动名。
    """
    out: dict[str, str] = {}
    try:
        rows = tables.read_any(src)
        # 找含「活动结束时间」的行
        for r in rows:
            labels = [i for i, c in enumerate(r)
                      if c is not None and "活动结束时间" in str(c)]
            if not labels:
                continue
            # 按块序号归位（每块 8 列）
            for blk, col in enumerate(labels):
                # 日期在标签右侧 2 列
                for j in (col + 2, col + 1, col + 3):
                    if j < len(r) and r[j] is not None and str(r[j]).strip():
                        if blk < len(ACTIVITIES):
                            out[ACTIVITIES[blk]] = str(r[j]).strip()
                        break
            break
    except Exception:
        pass
    return out


def _rate(num: Any, den: Any) -> Any:
    """比率计算（与案例精度一致）

    规则（实测自案例）：
      · 分母为空/0 → "-"
      · 分子为空    → "-"
      · 分子为 0    → "-"（案例规则：0 值不展示比率）
      · 其他        → 完整精度浮点
    """
    n, d = as_float(num), as_float(den)
    if d in (None, 0):
        return "-"
    if n is None:
        return "-"
    if n == 0:
        return "-"
    return round(n / d, 10)


def _blank(v: Any) -> bool:
    if v is None:
        return True
    s = str(v).strip()
    return s in ("", "-", "None", "nan", "0" if False else "\x00")
