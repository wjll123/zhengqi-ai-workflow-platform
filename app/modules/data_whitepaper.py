"""模块② 数据分析 —— 从两份到期月报生成白皮书 2.11。"""
from __future__ import annotations

from pathlib import Path
from ..core import tables
from ..core.config import get_batch
from ..core.logger import get_logger
from ..core.rules import as_float
from ..core.runner import TaskResult, registry

log = get_logger("data.whitepaper")

MODULE = "数据分析"

# 统一的白皮书输出文件（与资料包一致：政企码号台阶白皮书202608.xlsx）
# 三张表（1.6 / 2.11 / 3.5）分别由不同任务写入同一个文件的对应 sheet
OUT_WHITEPAPER = "政企码号台阶白皮书202608.xlsx"
WP_SHEET_ORDER = ["目录", "取数说明", "1.6", "2.11", "3.5"]

# 2.11 的 5 组业务 → (源文件关键词, 源工作表一级表头)
GROUPS = [
    ("移动到期", "话补"),
    ("移动到期", "终补"),
    ("双线到期", "双线到期"),
    ("双线到期", "组网到期"),
    ("双线到期", "互联网到期"),
]

# 2.11 输出表头
HEADER_ROW1 = ["区域", "地市", "移动到期续约", "", "", "", "", "", "", "", "", "", "双线到期续约", "", "", "", "", "", "", "", "", "", "", "", "", "", ""]
HEADER_ROW2 = ["", "", "对公政企话补到期", "", "", "", "", "终补到期", "", "", "", "", "双线到期", "", "", "", "", "组网专线到期", "", "", "", "", "互联网专线到期", "", "", "", ""]
HEADER_ROW3 = [""] * 27
for i in range(5):
    base = 2 + i * 5
    HEADER_ROW3[base:base + 5] = ["当年到期数", "累计续约数", "累计续约率", "当月续约数", "当月续约率"]

# 片区 → 区域（2.11 的「区域」列）
REGION_MAP = {
    "珠三角I": "珠一区", "珠三角Ⅱ": "珠二区", "珠三角II": "珠二区",
    "粤东": "粤东", "粤西": "粤西", "粤北": "粤北",
}


def _find_in_dir(inbox: Path, kw: str, batch: str) -> Path | None:
    if not inbox.exists():
        return None
    for p in sorted(inbox.iterdir()):
        if p.is_file() and kw in p.name and batch in p.name and not p.name.startswith(("~$", ".~")):
            return p
    return None


def _read_summary(path: Path, groups: list[str]) -> tuple[dict[str, dict], list[str]]:
    """读取 202608 格式的 Sheet2，按表头定位业务组，拒绝错位/空模板。"""
    rows = tables.read_any(path, "Sheet2")
    if len(rows) < 4:
        raise ValueError(f"{path.name} 的 Sheet2 行数不足")
    h1 = [str(v or "").strip() for v in rows[0]]
    h2 = [str(v or "").strip() for v in rows[1]]
    if "地市" not in h2 or "片区" not in h2:
        raise ValueError(f"{path.name} 的 Sheet2 缺少地市/片区列")
    city_col, region_col = h2.index("地市"), h2.index("片区")
    starts = {}
    for group in groups:
        choices = [i for i, v in enumerate(h1) if v == group and i < len(h2) and "当年到期数" in h2[i]]
        if len(choices) != 1:
            raise ValueError(f"{path.name} 的 Sheet2 无法唯一定位「{group}」数据列")
        starts[group] = choices[0]
    records: dict[str, dict] = {}
    order: list[str] = []
    for row in rows[2:]:
        city = str(row[city_col] or "").strip() if len(row) > city_col else ""
        if not city or city == "全省":
            continue
        if city in records:
            raise ValueError(f"{path.name} 的 Sheet2 地市重复：{city}")
        series = {}
        for group, base in starts.items():
            vals = [as_float(row[base + i]) if len(row) > base + i else None for i in range(4)]
            if any(v is None for v in vals):
                raise ValueError(f"{path.name} 的 Sheet2「{city} / {group}」存在空值或非数值")
            series[group] = vals
        records[city] = {"region": str(row[region_col] or "").strip(), "groups": series}
        order.append(city)
    if not records:
        raise ValueError(f"{path.name} 的 Sheet2 没有地市数据")
    return records, order


@registry.register(
    key="whitepaper_211",
    name="白皮书 2.11 重点业务续约率",
    module=MODULE,
    description="从「移动到期月报/Sheet2」+「双线到期月报/Sheet2」生成 5 组业务续约率",
    source_type="auto",
    source_note="CDAP 导出「移动到期月报」与「双线到期月报」，均需含 Sheet2 的数值列（勿交付空模板）",
    outputs_desc="政企码号台阶白皮书202608.xlsx → sheet「2.11」重点业务续约率（5 组业务 × 5 字段）",
    required_sources=[
        {"label": "移动到期月报（CDAP 导出，需含数值）", "keywords": ["移动到期"], "batch_key": True},
        {"label": "双线到期月报（CDAP 导出，需含数值）", "keywords": ["双线到期"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def whitepaper_211(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    batch = get_batch()
    f_mobile = _find_in_dir(inbox_path, "移动到期", batch)
    f_line = _find_in_dir(inbox_path, "双线到期", batch)

    if not f_mobile or not f_line:
        missing = []
        if not f_mobile:
            missing.append("移动到期月报")
        if not f_line:
            missing.append("双线到期月报")
        return TaskResult(ok=False, message=f"缺少源文件：{'、'.join(missing)}")

    try:
        mobile, cities = _read_summary(f_mobile, ["话补", "终补"])
        line, line_cities = _read_summary(f_line, ["双线到期", "组网到期", "互联网到期"])
    except (ValueError, KeyError) as exc:
        return TaskResult(ok=False, message=str(exc))
    if set(cities) != set(line_cities):
        return TaskResult(ok=False, message="移动和双线月报的地市集合不一致")

    body = []
    province = ["全省", "全省"]
    for kw, group in GROUPS:
        source = mobile if kw == "移动到期" else line
        due = sum(source[city]["groups"][group][0] for city in cities)
        renewed = sum(source[city]["groups"][group][1] for city in cities)
        current = sum(source[city]["groups"][group][3] for city in cities)
        province.extend([due, renewed, renewed / due * 100 if due else 0,
                         current, current / due * 100 if due else 0])
    body.append(province)

    last_region = None
    for city in cities:
        region = REGION_MAP.get(mobile[city]["region"], mobile[city]["region"])
        if not region:
            return TaskResult(ok=False, message=f"{city} 缺少片区")
        row = [region if region != last_region else None, city]
        last_region = region
        for kw, group in GROUPS:
            source = mobile if kw == "移动到期" else line
            due, renewed, cum_rate, current = source[city]["groups"][group]
            row.extend([due, renewed, cum_rate, current, current / due * 100 if due else 0])
        body.append(row)

    rows = [["重点业务续约率"] + [None] * 26,
            HEADER_ROW1, HEADER_ROW2, HEADER_ROW3] + body
    out = tables.upsert_sheet(
        OUTPUT_DIR / OUT_WHITEPAPER,
        "2.11",
        rows,
        WP_SHEET_ORDER,
    )

    return TaskResult(
        ok=True,
        message=f"生成完成：{len(body)} 行（1 全省 + {len(body) - 1} 地市）× 5 组业务（含当月续约率）",
        outputs=[str(out)],
        metrics={
            "源文件": f"{f_mobile.name} + {f_line.name}",
            "业务组数": 5,
            "地市数": len(body) - 1,
            "列数": 27,
            "当月续约率算法": "当月续约数 ÷ 当年到期数（口径来自白皮书月报PPT）",
            "数据填充率": "全部业务列已验证有数值",
        },
    )
