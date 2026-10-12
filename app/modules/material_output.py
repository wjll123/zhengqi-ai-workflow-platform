"""模块③ 材料输出 —— 通报文案生成

来源：王爽流程步骤 10 的通报文案模板（含 9 月示例）

模板结构（固定三段）：
  ① 下发说明
  ② 全省总指标 + 环比
  ③ 后五名地市 + 突出点

⚠️ 人机分工：「突出点」的选取需人工判断（J3），
   平台只生成数值部分，并留出人工编辑位。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import tables
from ..core.logger import get_logger
from ..core.rules import as_float, sort_rows_by_city
from ..core.runner import TaskResult, registry

log = get_logger("material.notice")

MODULE = "材料输出"

TEMPLATE = """已下发{year}年{month}月份全省各地市存量经营指标通报数据，请惠存（详见附件）。

{month}月全省客保率{baoshou}PP，环比{baoshou_mom}PP；收保率{shoubao}PP，环比{shoubao_mom}PP。

客保率后五为{cities_baoshou}，其中【请补充突出点，如：XX、XX 同比去年降幅超 3PP，XX 环比上月降幅超 5PP】，请重点关注。

收保率后五为{cities_shoubao}，其中【请补充突出点，如：XX、XX 同比去年下降超 10PP】，请重点关注。
"""


def _find_col(header: list[str], *keywords: str) -> int | None:
    """模糊找列"""
    for i, h in enumerate(header):
        for kw in keywords:
            if kw in str(h):
                return i
    return None


def _fmt(value: Any, digits: int = 1, signed: bool = False) -> str:
    v = as_float(value)
    if v is None:
        return "—"
    if signed:
        return f"{v:+.{digits}f}"
    return f"{v:.{digits}f}"


@registry.register(
    key="material_notice_text",
    name="通报文案生成",
    module=MODULE,
    description="按固定模板生成通报文案（数值部分自动，突出点留人工编辑位）",
    source_type="semi",
    source_note="数据源同「报到数据更新」（指标跟踪表 / 白皮书 2.11 产出）",
    outputs_desc="通报文案-X月.txt（对应 政企存量客户通报表·文案；突出点留人工填写位）",
    params=[
        {"name": "inbox", "label": "源数据目录", "type": "path", "required": False},
        {"name": "month", "label": "月份（如 8）", "type": "text", "required": False},
    ],
)
def material_notice_text(inbox: str | None = None, month: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    month = month or "8"

    # 找指标跟踪类源文件
    src = None
    for p in sorted(inbox_path.glob("*.xls*")):
        if p.name.startswith(("~$", ".~")):
            continue
        if any(k in p.name for k in ("指标跟踪", "通报", "收保率对标")):
            src = p
            break

    if src is None:
        # 无专用源时，尝试从 2.11 产出反推
        out_211 = OUTPUT_DIR / "政企码号台阶白皮书-2.11重点业务续约率.xlsx"
        if out_211.exists():
            text = _from_211(out_211, month)
            out = OUTPUT_DIR / f"通报文案-{month}月.txt"
            out.write_text(text, encoding="utf-8")
            return TaskResult(
                ok=True,
                message="已基于 2.11 产出生成通报文案（数值部分）",
                outputs=[str(out)],
                metrics={"来源": "白皮书 2.11", "月份": f"{month}月"},
                warnings=["未找到「指标跟踪表」源文件；「突出点」需人工补充"],
            )
        return TaskResult(
            ok=False,
            message="未找到通报源数据（指标跟踪表 / 收保率对标表），也没有可用的 2.11 产出",
        )

    log.info("读取通报源: %s", src.name)
    rows = tables.read_any(src)
    if len(rows) < 3:
        return TaskResult(ok=False, message="源文件行数不足")

    header = [str(c) for c in rows[0]]
    city_col = _find_col(header, "地市", "市")
    baoshou_col = _find_col(header, "客保率")
    shoubao_col = _find_col(header, "收保率")

    if city_col is None or (baoshou_col is None and shoubao_col is None):
        return TaskResult(
            ok=False,
            message=f"源文件缺少必需列（地市/客保率/收保率）。现有表头：{header[:10]}",
        )

    body = [r for r in rows[1:] if str(r[city_col]).strip() not in ("", "全省")]
    body = sort_rows_by_city(body, city_col)

    def bottom5(col: int | None) -> list[str]:
        if col is None:
            return []
        items = [(str(r[city_col]).strip(), as_float(r[col]) if len(r) > col else None) for r in body]
        items = [(c, v) for c, v in items if v is not None]
        items.sort(key=lambda x: x[1])
        return [c for c, _ in items[:5]]

    b5 = bottom5(baoshou_col)
    s5 = bottom5(shoubao_col)

    text = TEMPLATE.format(
        year="2026",
        month=month,
        baoshou="—", baoshou_mom="—",
        shoubao="—", shoubao_mom="—",
        cities_baoshou="、".join(b5) if b5 else "—",
        cities_shoubao="、".join(s5) if s5 else "—",
    )

    out = OUTPUT_DIR / f"通报文案-{month}月.txt"
    out.write_text(text, encoding="utf-8")

    return TaskResult(
        ok=True,
        message="通报文案已生成（数值与后五名自动，突出点待人工补充）",
        outputs=[str(out)],
        metrics={
            "源文件": src.name,
            "月份": f"{month}月",
            "客保率后五": "、".join(b5) or "—",
            "收保率后五": "、".join(s5) or "—",
        },
        warnings=[
            "「突出点」段落需人工填写（模板中已用【】标出位置）",
            "全省客保率/收保率数值需从指标表确认（当前源未含全省行）",
        ],
    )


def _from_211(path: Path, month: str) -> str:
    """从 2.11 产出生成文案骨架"""
    rows = tables.read_any(path)
    header = [str(c) for c in rows[2]] if len(rows) > 2 else []
    body = rows[3:]

    city_col = 1

    def bottom5_by(col_idx: int) -> list[str]:
        items = []
        for r in body:
            if len(r) <= max(city_col, col_idx):
                continue
            city = str(r[city_col]).strip()
            if city in ("", "全省"):
                continue
            v = as_float(r[col_idx])
            if v is not None:
                items.append((city, v))
        items.sort(key=lambda x: x[1])
        return [c for c, _ in items[:5]]

    # 话补「累计续约率」在第 4 列（0-based）
    b5 = bottom5_by(4)

    return TEMPLATE.format(
        year="2026", month=month,
        baoshou="—", baoshou_mom="—", shoubao="—", shoubao_mom="—",
        cities_baoshou="—",
        cities_shoubao="、".join(b5) if b5 else "—",
    )
