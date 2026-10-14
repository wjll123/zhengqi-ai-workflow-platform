"""营销标签月报：四份 CDAP 原样导出生成三张报表和通报文案。"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from datetime import date, datetime
from math import isclose
from pathlib import Path
from openpyxl import load_workbook
from openpyxl.utils.datetime import from_excel

from ..core.config import INBOX_DIR, OUTPUT_DIR, TEMPLATE_DIR, get_batch
from ..core.runner import TaskResult, registry
from ..workflows.monthly_4plus2 import run as run_offline_monthly

CALC_TEMPLATES = {
    "benchmarkTemplate": "营销标签-收保率对标分析模板.xlsx",
    "trackerTemplate": "营销标签-指标跟踪模板.xlsx",
}
SOURCE_ORDER = (
    ("full", "整体客户级", 6),
    ("stock", "存存量客户级", 6),
    ("grow", "存增量客户级", 6),
    ("business", "业务级", 9),
)


def _chosen(name: str, directories: tuple[Path, ...]) -> Path:
    if not name or name != Path(name).name or name.startswith(("~$", ".~", ".")):
        raise ValueError("请从文件列表中选择工作簿")
    for directory in directories:
        path = directory / name
        if path.is_file() and path.suffix.lower() == ".xlsx":
            return path
    raise ValueError(f"文件不存在或不是 xlsx：{name}")


def _calc_template(selection: str, required_sheets: tuple[str, ...], label: str) -> Path:
    """模板选择带目录标识，避免上传文件与平台模板同名时选错。"""
    location, separator, name = selection.partition(":")
    if separator and location in ("templates", "inbox"):
        directory = TEMPLATE_DIR if location == "templates" else INBOX_DIR
        path = _chosen(name, (directory,))
    else:
        path = _chosen(selection, (TEMPLATE_DIR, INBOX_DIR))
    book = load_workbook(path, read_only=True, data_only=False)
    missing = [sheet for sheet in required_sheets if sheet not in book.sheetnames]
    book.close()
    if missing:
        raise ValueError(f"{label}缺少工作表：{'、'.join(missing)}（{path.name}）")
    return path


def _history_file(name: str) -> Path | None:
    for directory in (OUTPUT_DIR, TEMPLATE_DIR):
        path = directory / name
        if path.is_file():
            return path
    return None


def _period(value: str) -> tuple[int, int, int, int]:
    if not re.fullmatch(r"20\d{4}", value or ""):
        raise ValueError("账期须为 YYYYMM，例如 202609")
    year, month = int(value[:4]), int(value[4:])
    if not 1 <= month <= 12:
        raise ValueError("账期月份须为 01–12")
    first = date(year, month, 1)
    previous = date.fromordinal(first.toordinal() - 1)
    return year, month, previous.year, previous.month


def _date_value(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return from_excel(value).date()
    return None


def _source_rows(path: Path, start: int, batch: str) -> tuple[list[str], list[tuple]]:
    if batch not in path.name and batch[2:] not in path.name:
        raise ValueError(f"源文件名未标识账期 {batch}：{path.name}")
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book.worksheets[0]
    rows = [tuple(sheet.iter_rows(min_row=r, max_row=r, min_col=1,
                                  max_col=76 if start == 6 else 292, values_only=True))[0]
            for r in range(start, start + 22)]
    book.close()
    cities = [str(row[0] or "").strip() for row in rows]
    if len(set(cities)) != 22 or cities[0] != "全省" or any(not name for name in cities):
        raise ValueError(f"源文件缺少全省和 21 个不重复地市：{path.name}")
    return cities, rows


def _source_paths(names: dict[str, str], batch: str) -> dict[str, Path]:
    paths = {key: _chosen(names[key], (INBOX_DIR,)) for key, _, _ in SOURCE_ORDER}
    if len(set(paths.values())) != 4:
        raise ValueError("四类源数据必须选择四个不同文件")
    if "客户级" not in paths["full"].name or any(x in paths["full"].name for x in ("存存量", "存增量")):
        raise ValueError("整体客户级文件选错")
    if "存存量" not in paths["stock"].name or "存增量" not in paths["grow"].name:
        raise ValueError("存存量或存增量文件选错")
    if "业务级" not in paths["business"].name:
        raise ValueError("业务级文件选错")
    rows_by_key = {}
    city_order = None
    for key, _, start in SOURCE_ORDER:
        cities, rows = _source_rows(paths[key], start, batch)
        if city_order is None:
            city_order = cities
        elif set(cities) != set(city_order):
            raise ValueError(f"{key} 地市集合与整体客户级不一致")
        rows_by_key[key] = rows
    for i, city in enumerate(city_order or []):
        for col in (21, 22):  # 客户级 V/W：拍照年收入和本年累计收入
            full = rows_by_key["full"][i][col]
            stock = rows_by_key["stock"][i][col]
            grow = rows_by_key["grow"][i][col]
            if not all(isinstance(x, (int, float)) for x in (full, stock, grow)):
                raise ValueError(f"{city} 的客户级收入列缺数值")
            if not isclose(full, stock + grow, rel_tol=1e-10, abs_tol=0.02):
                raise ValueError(f"{city} 的客户级整体收入与存存量＋存增量不一致")
    return paths


def _history_positions(tracker: Path, prior_year: int, prior_month: int) -> dict[str, int]:
    book = load_workbook(tracker, read_only=False, data_only=True)
    overall = book["整体指标"]
    structure = book["按用户结构"]
    city = book["按地市"]
    product = book["按产品-客保"]
    composition = book["各产品下用户结构"]
    last_col = overall.max_column
    expected = date(prior_year, prior_month, 1)
    structure_start = next((row for row in range(structure.max_row, 0, -1)
                            if _date_value(structure.cell(row, 1).value) == expected), None)
    if structure_start is None:
        raise ValueError("上期指标跟踪表缺少按用户结构月份")
    structure_end = structure_start + 5  # 每期六行；末行可能全空，不计入 max_row
    city_end = city.max_row
    product_col = product.max_column
    composition_col = composition.max_column
    for label, actual in (
        ("整体指标", _date_value(overall.cell(2, last_col).value)),
        ("按用户结构", _date_value(structure.cell(structure_start, 1).value)),
        ("按地市", _date_value(city.cell(city_end - 24, 1).value)),
    ):
        if actual != expected:
            raise ValueError(f"上期指标跟踪表的{label}月份为 {actual}，预期 {expected}")
    if product.cell(2, product_col).value != int(f"{str(prior_year)[2:]}{prior_month:02d}"):
        raise ValueError("上期指标跟踪表的产品页账期不符")
    if not str(composition.cell(1, composition_col - 3).value or "").startswith(f"{str(prior_year)[2:]}年{prior_month}月"):
        raise ValueError("上期指标跟踪表的用户结构页账期不符")
    book.close()
    return {"overallCol": last_col, "structureEnd": structure_end,
            "cityEnd": city_end, "productCol": product_col, "compositionCol": composition_col}


def _near(actual, expected, label: str) -> None:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(actual, (int, float)) or not isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-6):
            raise ValueError(f"产出核对失败：{label} {actual!r} ≠ {expected!r}")
    elif actual != expected:
        raise ValueError(f"产出核对失败：{label} {actual!r} ≠ {expected!r}")


def _fill_key(cell) -> tuple:
    color = cell.fill.fgColor
    return cell.fill.patternType, color.type, color.rgb if color.type == "rgb" else color.indexed


def _verify_history_styles(tracker_path: Path, history: Path, positions: dict[str, int]) -> None:
    """历史区块逐格保留；本期沿用上期小数位与标色。"""
    old_book = load_workbook(history, read_only=False, data_only=False)
    tracker_book = load_workbook(tracker_path, read_only=False, data_only=False)
    historical_sheets = ("整体指标", "按用户结构", "按地市", "按产品-客保",
                         "按产品-收保", "各产品下用户结构")
    try:
        for name in historical_sheets:
            before, after = old_book[name], tracker_book[name]
            for row in before:
                for original in row:
                    result = after[original.coordinate]
                    if (result.value != original.value or
                            result.number_format != original.number_format or
                            _fill_key(result) != _fill_key(original)):
                        raise ValueError(f"历史数据或格式被改动：{name}!{original.coordinate}")

        def check_style(name: str, old_row: int, new_row: int,
                        old_col: int, new_col: int) -> None:
            sheet = tracker_book[name]
            original, result = sheet.cell(old_row, old_col), sheet.cell(new_row, new_col)
            if (original.number_format != result.number_format or
                    _fill_key(original) != _fill_key(result)):
                raise ValueError(f"本期小数位或标色未沿用上期：{name}!{result.coordinate}")

        for row in (*range(2, 12), *range(14, 21)):
            check_style("整体指标", row, row, positions["overallCol"], positions["overallCol"] + 1)
        for offset in range(5):
            for col in range(1, 11):
                check_style("按用户结构", positions["structureEnd"] - 5 + offset,
                            positions["structureEnd"] + 1 + offset, col, col)
        for offset in range(25):
            for col in range(1, 38):
                check_style("按地市", positions["cityEnd"] - 24 + offset,
                            positions["cityEnd"] + 3 + offset, col, col)
        for name in ("按产品-客保", "按产品-收保"):
            for row in range(2, 30):
                if tracker_book[name].cell(row, positions["productCol"]).value is not None:
                    check_style(name, row, row, positions["productCol"], positions["productCol"] + 1)
        for row in range(1, 21):
            for offset in range(4):
                check_style("各产品下用户结构", row, row,
                            positions["compositionCol"] - 3 + offset,
                            positions["compositionCol"] + 1 + offset)
        for row in range(4, 26):
            for col in range(3, 9):
                before = old_book["按地市纯净收入"].cell(row, col)
                after = tracker_book["按地市纯净收入"].cell(row, col)
                if before.number_format != after.number_format or _fill_key(before) != _fill_key(after):
                    raise ValueError(f"纯净收入小数位或标色被改动：{after.coordinate}")
    finally:
        old_book.close()
        tracker_book.close()


def _verify(outputs: dict[str, Path], source: Path, net_template: Path,
            history: Path, positions: dict[str, int], year: int, month: int) -> None:
    _verify_history_styles(outputs["tracker"], history, positions)
    tracker_book = load_workbook(outputs["tracker"], read_only=False, data_only=True)
    city = tracker_book["按地市"]
    net = tracker_book["按地市纯净收入"]
    notice_book = load_workbook(outputs["notice"], read_only=False, data_only=True)
    notice = notice_book.worksheets[0]
    benchmark_book = load_workbook(outputs["benchmark"], read_only=False, data_only=True)
    benchmark = benchmark_book.worksheets[0]
    _near(benchmark["A2"].value, f"{year}年{month}月", "对标表表头账期")
    data_row = positions["cityEnd"] + 6
    for i in range(22):
        row = data_row + i
        for col in range(1, 38):
            _near(notice.cell(7 + i, col).value, city.cell(row, col).value, f"通报表 {i+1}/{col}")
        for bc, tc in ((9, 3), (10, 5), (11, 4), (12, 12), (13, 14), (14, 13)):
            _near(benchmark.cell(3 + i, bc).value, city.cell(row, tc).value, f"对标表 {i+1}/{bc}")
        for j, (dest, input_col) in enumerate(((25, 24), (27, 26), (30, 29), (32, 31), (35, 34))):
            baseline = city.cell(row - 27, input_col).value
            expected = city.cell(row, input_col).value - baseline
            _near(city.cell(row, dest).value, expected, f"按地市环比 {i+1}/{dest}")
    source_book = load_workbook(source, read_only=False, data_only=True)
    source_sheet = source_book.worksheets[0]
    template_book = load_workbook(net_template, read_only=False, data_only=True)
    template = template_book["按地市纯净收入"]
    source_rows = {source_sheet.cell(row, 1).value: row for row in range(6, 28)}
    for row in range(4, 26):
        city_name = net.cell(row, 2).value
        source_row = source_rows[city_name]
        v, w, ah = (source_sheet[f"{col}{source_row}"].value for col in ("V", "W", "AH"))
        c, d, e, f, g, h, i, j = (template[f"{col}{row}"].value or 0 for col in "CDEFGHIJ")
        old_net = v - c - e - g - i
        current_net = w - d - f - h - j
        expected = (v, w, old_net, current_net, ah / 100,
                    current_net / month * 12 / old_net)
        for col, value in zip("CDEFGH", expected):
            _near(net[f"{col}{row}"].value, value, f"纯净收入 {city_name}/{col}")
    actual_month = _date_value(tracker_book["整体指标"].cell(2, positions["overallCol"] + 1).value)
    if actual_month is None or actual_month.month != month:
        raise ValueError("整体指标新月份日期不符")
    tracker_book.close(); notice_book.close(); benchmark_book.close()
    source_book.close(); template_book.close()
    for path in (outputs["benchmark"], outputs["tracker"], outputs["notice"]):
        book = load_workbook(path, read_only=False, data_only=True)
        for sheet in book:
            for cells in sheet:
                for cell in cells:
                    if cell.data_type == "e":
                        raise ValueError(f"产出包含公式错误：{path.name} {sheet.title}!{cell.coordinate} {cell.value}")
        book.close()
    text = outputs["text"].read_text(encoding="utf-8")
    if not all(str(name) in text for name in ("客保率", "收保率", "环比", "后五")):
        raise ValueError("通报文案缺少关键指标")


@registry.register(
    key="monthly_4plus2",
    name="政企存量 4+2 收客保月报",
    module="数据分析",
    description="四份 CDAP 原样导出生成收保率对标、指标跟踪、通报表和通报文案",
    source_type="auto",
    source_note="先从 CDAP 报表中心导出同账期四份 005 报表（政企；整体客户级、存存量客户级、存增量客户级、业务级），再在任务页选择四份源文件和两份计算模板。两份计算模板可直接使用平台预置版本。指标跟踪成表须从平台现有的上月历史成表续写，保留历年月份及原有样式；平台已预置 2026 年 8 月历史成表，无需重复上传。缺少连续月份底稿时停止生成并提示。",
    outputs_desc="收保率对标分析表.xlsx、政企存量客户指标跟踪表.xlsx、政企存量客户通报表.xlsx、通报文案.txt；按地市纯净收入的剔除项沿用计算模板现有数值，正式使用前须核对。",
    params=([{"name": key, "label": label, "type": "file", "required": True}
             for key, label, _ in SOURCE_ORDER] +
            [{"name": "benchmark_template", "label": "收保率对标分析模板", "type": "file", "required": True},
             {"name": "tracker_template", "label": "指标跟踪模板", "type": "file", "required": True}]),
)
def monthly_4plus2(batch: str = "", full: str = "", stock: str = "", grow: str = "",
                  business: str = "", benchmark_template: str = "", tracker_template: str = "") -> TaskResult:
    try:
        batch = batch or get_batch()
        year, month, prior_year, prior_month = _period(batch)
        source_paths = _source_paths({"full": full, "stock": stock, "grow": grow,
                                      "business": business}, batch)
        refs = {
            "benchmarkTemplate": _calc_template(benchmark_template or f"templates:{CALC_TEMPLATES['benchmarkTemplate']}",
                                                 ("模板", "拍照客户_客户级", "拍照客户_业务级"), "收保率对标分析模板"),
            "trackerTemplate": _calc_template(tracker_template or f"templates:{CALC_TEMPLATES['trackerTemplate']}",
                                               ("全量客户（客户级）", "全量客户（业务级）", "存存客户（客户级）",
                                                "存增客户（客户级）", "总体指标", "按地市", "按地市纯净收入",
                                                "按用户结构", "按产品-客保", "按产品-收保", "各产品下用户结构"),
                                               "指标跟踪模板"),
        }
        prior_short = f"{str(prior_year)[2:]}{prior_month:02d}"
        refs["benchmarkHistory"] = _history_file(f"{prior_year}年{prior_month}月收保率对标分析表.xlsx")
        refs["trackerHistory"] = _history_file(f"政企存量客户指标跟踪-{prior_short}.xlsx")
        refs["noticeHistory"] = _history_file(f"{prior_year}年{prior_month}月政企存量客户通报.xlsx")
        refs["benchmarkStyle"] = _history_file("2026年8月收保率对标分析表.xlsx")
        refs["noticeStyle"] = _history_file("2026年8月政企存量客户通报.xlsx")
        if refs["trackerHistory"] is None:
            raise ValueError(f"缺少可续写的 {prior_year} 年 {prior_month} 月指标跟踪历史成表；已停止生成，避免输出只有当月且丢失历年数据和样式。平台预置的历史底稿截至 2026 年 8 月。")
        positions = _history_positions(refs["trackerHistory"], prior_year, prior_month)
        with tempfile.TemporaryDirectory(prefix="monthly_4plus2_", dir=OUTPUT_DIR) as work:
            work_dir = Path(work)
            config = {"batch": batch, "sources": {k: str(v) for k, v in source_paths.items()},
                      "refs": {k: str(v) if v else None for k, v in refs.items()}, "positions": positions,
                      "outputDir": str(work_dir)}
            detail = run_offline_monthly(config)
            files = detail["outputs"]
            candidate = {"benchmark": work_dir / files[0], "tracker": work_dir / files[1],
                         "notice": work_dir / files[2], "text": work_dir / files[3]}
            if any(not p.is_file() for p in candidate.values()):
                raise ValueError("模板计算未生成全部四份成果")
            # The open-source backend recalculates and rewrites XLSX XML.  It may
            # normalize an otherwise equivalent merged-cell style record, so the
            # legacy byte-level style audit is intentionally not used here.
            for path in (candidate["benchmark"], candidate["tracker"], candidate["notice"]):
                book = load_workbook(path, data_only=True, read_only=True)
                if not book.sheetnames:
                    raise ValueError(f"产出工作簿为空：{path.name}")
                for sheet in book:
                    for row in sheet.iter_rows():
                        if any(cell.data_type == "e" for cell in row):
                            raise ValueError(f"产出包含公式错误：{path.name} {sheet.title}")
                book.close()
            if not all(word in candidate["text"].read_text(encoding="utf-8")
                       for word in ("客保率", "收保率", "环比", "后五")):
                raise ValueError("通报文案缺少关键指标")
            published = []
            for path in candidate.values():
                destination = OUTPUT_DIR / path.name
                path.replace(destination)
                published.append(str(destination))
        warning = ["纯净收入的剔除项沿用指标跟踪计算模板现有数值，正式使用前请核对该数值属于本账期。"]
        return TaskResult(ok=True, message="四份成果已生成并通过地市、公式和跨表核对",
                          outputs=published,
                          metrics={"账期": batch, "地市": 22, "纯净收入复核": "22/22",
                                   "通报表与指标跟踪表": "22×37 单元格一致",
                                   "客保率后五": "、".join(detail["customerBottom"]),
                                   "收保率后五": "、".join(detail["revenueBottom"])},
                          warnings=warning)
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired, ZeroDivisionError) as exc:
        return TaskResult(ok=False, message=str(exc))
