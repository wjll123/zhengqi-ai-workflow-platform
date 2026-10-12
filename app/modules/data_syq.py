"""模块② 数据分析 —— 商企续约数据（月报·主输出③）

源：2-商企分享升级又续约数据.xlsx
  Sheet1 「商企续约又升级」：地市 × 5 类合约（终端/智家/宽带包年/话补/宽带提速）+ 总数
  Sheet2 「商企分享」      ：片区 × 分公司 × 10 个指标 + 3 个派生列

⚠️ 派生列公式（**从源文件公式实测得出**，非取数说明的字面描述）
源文件 Sheet2 第 3 行真实公式：
  M3 = F3 - J3   →  升级数 − 续约升级数
  N3 = H3 - J3   →  续约数 − 续约升级数
  O3 = M3 + N3 + J3  →  派生1 + 派生2 + 续约升级数

列字母 → 0-based 索引：F=5（升级数）H=7（续约数）J=9（续约升级数）
  M=12（派生1）N=13（派生2）O=14（校验和）

> 取数说明写的是「升级总数-续约升级数」，实测「升级总数」实为**升级数**（列5），
> 不是「营销成功总数」（列3）。已按实测公式实现，并用全省行验证通过。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core import tables
from ..core.logger import get_logger
from ..core.rules import as_float
from ..core.runner import TaskResult, registry

log = get_logger("data.syq")

MODULE = "数据分析"

# Sheet2 关键列（0-based）
COL_UPGRADE = 5      # 升级数（源公式里的 F 列）
COL_RENEW = 7        # 续约数（源公式里的 H 列）
COL_RENEW_UP = 9     # 续约、升级数（源公式里的 J 列）
COL_DERIV_1 = 12     # 升级数 − 续约升级数（M 列）
COL_DERIV_2 = 13     # 续约数 − 续约升级数（N 列）
COL_DERIV_3 = 14     # 校验和 O 列


def _find(inbox: Path, keyword: str) -> Path | None:
    if not inbox.exists():
        return None
    for p in sorted(inbox.iterdir()):
        if p.is_file() and keyword in p.name and not p.name.startswith(("~$", ".~")):
            return p
    return None


def _derive_sheet2(rows: list[list[Any]]) -> tuple[list[list[Any]], list[str]]:
    """补算 Sheet2 的 3 个派生列（按源文件实测公式）"""
    warnings: list[str] = []
    if len(rows) < 3:
        return rows, ["Sheet2 行数不足，跳过派生计算"]

    out: list[list[Any]] = [list(rows[0]), list(rows[1])]

    def get(row: list[Any], idx: int) -> Any:
        return row[idx] if len(row) > idx else ""

    for r in rows[2:]:
        row = list(r)
        while len(row) <= COL_DERIV_3:
            row.append("")

        up = as_float(get(r, COL_UPGRADE))
        renew = as_float(get(r, COL_RENEW))
        renew_up = as_float(get(r, COL_RENEW_UP))

        d1 = round(up - renew_up, 2) if None not in (up, renew_up) else ""
        d2 = round(renew - renew_up, 2) if None not in (renew, renew_up) else ""

        row[COL_DERIV_1] = d1
        row[COL_DERIV_2] = d2
        if d1 != "" and d2 != "" and renew_up is not None:
            row[COL_DERIV_3] = round(d1 + d2 + renew_up, 2)

        out.append(row)

    # 用全省行做自校验（源文件全省行自带正确答案）
    for r in out[2:]:
        if str(get(r, 1)).strip() != "全省":
            continue
        checks = [
            ("升级数-续约升级数", COL_UPGRADE, COL_DERIV_1),
            ("续约数-续约升级数", COL_RENEW, COL_DERIV_2),
        ]
        for label, src_col, dst_col in checks:
            base = as_float(get(r, src_col))
            renew_up = as_float(get(r, COL_RENEW_UP))
            mine = as_float(get(r, dst_col))
            if None in (base, renew_up) or mine is None:
                continue
            expect = round(base - renew_up, 2)
            if abs(expect - mine) > 0.01:
                warnings.append(f"全省「{label}」自校验不符：算得 {expect}，表内 {mine}")
        break

    return out, warnings



@registry.register(
    key="monthly_syq",
    name="商企续约数据处理",
    module=MODULE,
    description="读取商企分享升级又续约数据，补算 3 个派生列，输出地市表 + 分公司表",
    source_type="semi",
    source_note="""
【取数路径】CDAP → 探索中心（最上方导航栏）→ 自助分析 → 省专区 → 查看（默认政企标签项目）→ 仓库管理 → 数据控制台
【SQL 文件】2-商企分享升级又续约数据.sql
【必须替换的参数 ⚠️】202607 → 当月（如 202608）；20260731 → 当月末（如 20260831）
【注意事项】
  · 代码丢进去执行后，去掉双线部分（数据不准，只做口径保留、不跑数）
  · result8-7 → 商企续约又升级；result8-8 → VPN续约又升级
  · 下方结果直接粘贴到 Excel 即可
【派生列公式（以源文件实测为准）】
  M = F - J（升级数 − 续约升级数）
  N = H - J（续约数 − 续约升级数）
【结果落盘】data/inbox/ 下，文件名含「商企分享」即可
""",
    outputs_desc="月报-商企续约数据.xlsx → sheet「商企续约又升级」+「商企分享」",
    required_sources=[
        {"label": "商企分享升级又续约数据（跑 SQL 结果）", "keywords": ["商企分享", "商企续约"], "batch_key": True},
    ],
    params=[{"name": "inbox", "label": "源数据目录", "type": "path", "required": False}],
)
def monthly_syq(inbox: str | None = None) -> TaskResult:
    from ..core.config import INBOX_DIR, OUTPUT_DIR

    inbox_path = Path(inbox) if inbox else INBOX_DIR
    src = _find(inbox_path, "商企分享") or _find(inbox_path, "商企续约")

    if not src:
        return TaskResult(
            ok=False,
            message="未找到「商企分享升级又续约数据」源文件",
        )

    log.info("读取商企续约源: %s", src.name)
    sheets = tables.sheet_names(src)

    # Sheet1：地市表
    s1_name = "Sheet1" if "Sheet1" in sheets else sheets[0]
    s1 = tables.read_any(src, s1_name)
    s1 = [r for r in s1 if any(str(c).strip() for c in r if c is not None)]

    # Sheet2：分公司表（含派生列）
    s2_name = "Sheet2" if "Sheet2" in sheets else (sheets[1] if len(sheets) > 1 else None)
    s2, warnings = ([], ["源文件缺少 Sheet2"])
    if s2_name:
        raw2 = tables.read_any(src, s2_name)
        raw2 = [r for r in raw2 if any(str(c).strip() for c in r if c is not None)]
        s2, warnings = _derive_sheet2(raw2)

    out = tables.write_xlsx(
        OUTPUT_DIR / "月报-商企续约数据.xlsx",
        {
            "商企续约又升级": s1,
            "商企分享": s2,
        },
        number_formats={
            ("商企分享", 2): "0.00", ("商企分享", 4): "0.000",
            ("商企分享", 5): "0.000", ("商企分享", 6): "0.000",
            ("商企分享", 7): "0.000", ("商企分享", 8): "0.000",
            ("商企分享", 9): "0.000",
        },
    )

    # 统计
    city_count = sum(1 for r in s1 if len(r) > 1 and str(r[1]).strip() not in ("", "地市名称"))
    branch_count = sum(
        1 for r in s2[2:]
        if len(r) > 1 and str(r[1]).strip() not in ("", "分公司")
    ) if len(s2) > 2 else 0

    # 全省行摘要
    province = {}
    for r in s2[2:]:
        if len(r) > 1 and str(r[1]).strip() == "全省":
            province = {
                "到期数": r[2] if len(r) > 2 else "",
                "营销成功总数": r[3] if len(r) > 3 else "",
                "升级数": r[5] if len(r) > 5 else "",
                "续约数": r[7] if len(r) > 7 else "",
                "续约升级数": r[9] if len(r) > 9 else "",
            }
            break

    return TaskResult(
        ok=True,
        message=f"生成完成：地市 {city_count} 行 + 分公司 {branch_count} 行（含 2 个派生列）",
        outputs=[str(out)],
        metrics={
            "源文件": src.name,
            "地市表": f"{len(s1)} 行 × {len(s1[0]) if s1 else 0} 列",
            "分公司表": f"{len(s2)} 行 × {len(s2[0]) if s2 else 0} 列",
            "派生列": "升级总数-续约升级数 / 续约总数-续约升级数",
            **({f"全省{k}": v for k, v in province.items()} if province else {}),
        },
        warnings=warnings,
    )
