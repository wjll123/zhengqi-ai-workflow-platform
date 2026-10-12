"""数据预览：把产出的表格转成网页可渲染的结构

用于「点按钮看数据表格」——不下载文件也能在页面上直接查看。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, jsonify, request

from .core import tables
from .core.config import OUTPUT_DIR, REFERENCE_DIR
from .core.logger import get_logger

log = get_logger("preview")

bp = Blueprint("preview", __name__)

MAX_ROWS = 300       # 单次最多返回行数（防大表卡浏览器）
MAX_COLS = 60        # 单次最多返回列数


def _safe_path(name: str, scope: str = "outputs") -> Path | None:
    """只允许预览成品或只读的数据源样例。"""
    root = REFERENCE_DIR if scope == "reference" else OUTPUT_DIR if scope == "outputs" else None
    if root is None:
        return None
    p = (root / name).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p if p.exists() and p.is_file() else None


@bp.get("/api/preview/<path:name>")
def api_preview(name: str):
    """预览产出文件（xlsx / xls / csv / txt）"""
    scope = request.args.get("scope", "outputs")
    p = _safe_path(name, scope)
    if p is None:
        return jsonify({"ok": False, "error": "文件不存在或无权访问"}), 404

    sheet = request.args.get("sheet") or None
    offset = int(request.args.get("offset", 0))

    suffix = p.suffix.lower()

    # 文本文件
    if suffix in (".txt", ".md", ".log"):
        text = p.read_text(encoding="utf-8", errors="replace")
        return jsonify({
            "ok": True, "type": "text", "file": p.name,
            "content": text, "size": p.stat().st_size, "scope": scope,
        })

    # 表格文件
    try:
        sheets = ["数据"] if suffix == ".csv" else tables.sheet_names(p)
        target = sheet if (sheet and sheet in sheets) else sheets[0]
        rows = tables.read_any(p, None if suffix == ".csv" else target)
    except Exception as e:
        log.warning("预览失败 %s: %s", p.name, e)
        return jsonify({"ok": False, "error": f"无法读取：{e}"}), 400

    total = len(rows)

    # 头部（用于识别复合表头），取前若干行
    head = rows[: min(6, total)]
    header_count = _detect_header_rows(
        [[tables.cell_to_text(c) for c in r[:MAX_COLS]] for r in head]
    ) if head else 1
    header_count = max(1, header_count)

    # 数据区（跳过表头）
    data_rows = rows[header_count:]
    total_data = len(data_rows)

    # 分页：offset 以「数据行」为单位
    page = data_rows[offset: offset + MAX_ROWS]

    def render(rs: list[list]) -> list[list[str]]:
        out = []
        for r in rs:
            row = [tables.cell_to_text(c) for c in r[:MAX_COLS]]
            if len(r) > MAX_COLS:
                row.append(f"…（共 {len(r)} 列）")
            out.append(row)
        return out

    header_rows = render(head[:header_count])

    return jsonify({
        "ok": True,
        "type": "table",
        "file": p.name,
        "sheets": sheets,
        "sheet": target,
        "header_rows": header_rows,
        "header": header_rows[0] if header_rows else [],
        "rows": render(page),
        "total_rows": total_data,
        "total_cols": len(rows[0]) if rows else 0,
        "shown_rows": len(page),
        "offset": offset,
        "limit": MAX_ROWS,
        "has_more": offset + MAX_ROWS < total_data,
        "truncated": total_data > MAX_ROWS,
        "size": p.stat().st_size,
        "scope": scope,
    })


def _detect_header_rows(body: list[list[str]], max_scan: int = 4) -> int:
    """识别复合表头的行数

    规则：从第 2 行起，若该行在第 0 列（通常是"地市/单位"这类维度列）
    仍为空，且下方存在数据行，则它属于表头组。
    """
    if not body:
        return 0
    count = 1
    for i in range(1, min(max_scan, len(body))):
        row = body[i]
        first = (row[0] if row else "").strip()
        filled = sum(1 for c in row if c.strip())
        # 首列为空 且 有内容 → 疑似副表头
        if not first and filled > 0:
            count += 1
        else:
            break
    return count


@bp.get("/api/sheets/<path:name>")
def api_sheets(name: str):
    """列出文件的 sheet"""
    p = _safe_path(name)
    if p is None:
        return jsonify({"ok": False, "error": "文件不存在"}), 404
    try:
        return jsonify({"ok": True, "sheets": tables.sheet_names(p)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400
