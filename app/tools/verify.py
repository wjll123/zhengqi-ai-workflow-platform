"""自检工具：把平台输出与人工成品逐格比对

这是平台质量的守门员 —— 有现成正确答案时，必须逐格对得上。

用法：
  python -m app.tools.verify                  # 自动找参考文件
  python -m app.tools.verify --ref <path>     # 指定参考文件
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..core import tables
from ..core.config import OUTPUT_DIR
from ..core.logger import get_logger

log = get_logger("verify")


def _norm(v) -> str:
    return tables.cell_to_text(v)


def compare_sheet(
    mine_rows: list[list],
    ref_rows: list[list],
    key_cols: list[int],
    label: str,
) -> dict:
    """比对两个表（以 key_cols 为联合主键匹配行）"""
    result = {
        "label": label,
        "header_same": False,
        "ref_rows": max(0, len(ref_rows) - 1),
        "mine_rows": max(0, len(mine_rows) - 1),
        "matched": 0,
        "value_diff": 0,
        "not_found": 0,
        "examples": [],
    }
    if not mine_rows or not ref_rows:
        return result

    result["header_same"] = [_norm(x) for x in mine_rows[0]] == [_norm(x) for x in ref_rows[0]]

    def key_of(row):
        return tuple(_norm(row[c]) if c < len(row) else "" for c in key_cols)

    index = {}
    for r in mine_rows[1:]:
        index.setdefault(key_of(r), r)

    for r in ref_rows[1:]:
        k = key_of(r)
        if k not in index:
            result["not_found"] += 1
            continue
        a = [_norm(x) for x in index[k]]
        b = [_norm(x) for x in r]
        if a[: len(b)] == b:
            result["matched"] += 1
        else:
            result["value_diff"] += 1
            if len(result["examples"]) < 3:
                diff_cols = [i for i, (x, y) in enumerate(zip(a, b)) if x != y][:5]
                result["examples"].append({
                    "key": k,
                    "diff_columns": diff_cols,
                    "mine": [a[i] for i in diff_cols],
                    "ref": [b[i] for i in diff_cols],
                })
    return result


def run_verify(ref_file: str | None = None) -> int:
    """执行比对，返回失败数"""
    ref_path = Path(ref_file) if ref_file else None
    if ref_path is None or not ref_path.exists():
        log.warning("未提供参考文件（--ref），跳过逐格比对")
        log.info("平台产出目录：%s", OUTPUT_DIR)
        for p in sorted(OUTPUT_DIR.glob("*.xlsx")):
            rows = tables.read_any(p)
            log.info("  · %s  (%d 行 × %d 列)", p.name, len(rows), len(rows[0]) if rows else 0)
        return 0

    log.info("参考文件：%s", ref_path)
    ref_sheets = tables.sheet_names(ref_path)
    log.info("参考 sheets：%s", ref_sheets)

    cases = [
        ("841营销活动管控表加工", "1-接单情况模板-841营销活动管控表.xlsx", "841营销活动管控表", [0, 1, 2]),
        ("活动评估搬运", "1-接单情况模板-智慧中台活动评估.xlsx", "智慧中台活动评估", [0, 1]),
        ("调度工单加工", "1-接单情况模板-智慧中台调度工单查询.xlsx", "智慧中台调度工单查询", [0]),
        ("接触工单加工", "1-接单情况模板-智慧中台接触工单查询.xlsx", "智慧中台接触工单查询", [0]),
    ]

    failures = 0
    print()
    print("=" * 78)
    print("逐格比对报告")
    print("=" * 78)

    for label, mine_name, ref_sheet, key_cols in cases:
        mine_path = OUTPUT_DIR / mine_name
        if not mine_path.exists():
            print(f"\n【{label}】 ⏭  平台未产出（{mine_name}）")
            continue
        if ref_sheet not in ref_sheets:
            print(f"\n【{label}】 ⏭  参考文件无此 sheet")
            continue

        mine = tables.read_any(mine_path)
        ref = tables.read_any(ref_path, ref_sheet)
        r = compare_sheet(mine, ref, key_cols, label)

        ok = r["header_same"] and r["not_found"] == 0 and r["value_diff"] == 0
        mark = "✅" if ok else "⚠️ "
        print(f"\n【{label}】 {mark}")
        print(f"   表头一致   : {'是' if r['header_same'] else '否'}")
        print(f"   参考行数   : {r['ref_rows']}  |  平台行数: {r['mine_rows']}")
        print(f"   完全一致   : {r['matched']}")
        print(f"   值不同     : {r['value_diff']}")
        print(f"   平台未找到 : {r['not_found']}")
        for ex in r["examples"]:
            print(f"     · key={ex['key']} 差异列{ex['diff_columns']}")
            print(f"       平台={ex['mine']}")
            print(f"       人工={ex['ref']}")

        if not ok and r["not_found"] > 0 and r["not_found"] == r["ref_rows"]:
            print("   ⓘ 全部未匹配 → 可能源数据与人工成品来自不同日期快照（非代码问题）")
        if not ok and r["header_same"] is False:
            failures += 1

    print()
    print("=" * 78)
    print(f"表头不一致项：{failures}（其余差异需人工判断是否数据快照不同导致）")
    print("=" * 78)
    return failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="平台输出自检")
    ap.add_argument("--ref", help="人工成品参考文件路径")
    args = ap.parse_args(argv)
    return run_verify(args.ref)


if __name__ == "__main__":
    sys.exit(main())
