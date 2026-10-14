"""Offline monthly workbook calculation using LibreOffice and openpyxl."""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request
from copy import copy
from datetime import date, datetime
from pathlib import Path
from zipfile import ZipFile

from lxml import etree
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
from openpyxl.utils.datetime import to_excel


CITIES = (
    "全省", "深圳", "广州", "东莞", "佛山", "中山", "惠州", "江门", "珠海",
    "汕头", "揭阳", "潮州", "汕尾", "茂名", "湛江", "阳江", "云浮",
    "清远", "肇庆", "梅州", "韶关", "河源",
)
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _values(sheet, min_row: int, max_row: int, min_col: int, max_col: int) -> list[list]:
    return [[sheet.cell(row, col).value for col in range(min_col, max_col + 1)]
            for row in range(min_row, max_row + 1)]


def _write(sheet, row: int, col: int, values: list[list]) -> None:
    for ri, values_row in enumerate(values):
        for ci, value in enumerate(values_row):
            sheet.cell(row + ri, col + ci).value = value


def _copy_range(sheet, src_row: int, dst_row: int, min_col: int, max_col: int,
                rows: int, src_col: int | None = None) -> None:
    src_col = min_col if src_col is None else src_col
    for ri in range(rows):
        for ci in range(max_col - min_col + 1):
            original = sheet.cell(src_row + ri, src_col + ci)
            result = sheet.cell(dst_row + ri, min_col + ci)
            result.value = original.value
            if original.has_style:
                result._style = copy(original._style)
            if original.number_format:
                result.number_format = original.number_format
            if original.comment:
                result.comment = copy(original.comment)
            if original.hyperlink:
                result._hyperlink = copy(original.hyperlink)


def _set_formula_cache(path: Path, original: Path | None,
                       new_values: dict[str, dict[str, object]]) -> None:
    """Retain old formula caches and fill caches for the new comparison formulas."""
    workbook = load_workbook(path, data_only=False)
    old_formulas = load_workbook(original, data_only=False) if original else None
    old_values = load_workbook(original, data_only=True) if original else None
    cache: dict[int, dict[str, object]] = {}
    for index, sheet in enumerate(workbook.worksheets, 1):
        values = dict(new_values.get(sheet.title, {}))
        if old_formulas and sheet.title in old_formulas:
            before = old_formulas[sheet.title]
            calculated = old_values[sheet.title]
            for row in before:
                for cell in row:
                    if (cell.data_type == "f" and
                            sheet[cell.coordinate].value == cell.value and
                            calculated[cell.coordinate].value is not None):
                        values.setdefault(cell.coordinate, calculated[cell.coordinate].value)
        if values:
            cache[index] = values
    workbook.close()
    if old_formulas:
        old_formulas.close()
        old_values.close()
    if not cache:
        return

    replacement = path.with_suffix(".cached.xlsx")
    with ZipFile(path) as source, ZipFile(replacement, "w") as target:
        for item in source.infolist():
            payload = source.read(item.filename)
            if item.filename.startswith("xl/worksheets/sheet") and item.filename.endswith(".xml"):
                index_text = item.filename.removeprefix("xl/worksheets/sheet").removesuffix(".xml")
                values = cache.get(int(index_text)) if index_text.isdigit() else None
                if values:
                    root = etree.fromstring(payload)
                    for cell in root.iter(f"{{{NS}}}c"):
                        value = values.get(cell.get("r"))
                        if value is None or cell.find(f"{{{NS}}}f") is None:
                            continue
                        cached = cell.find(f"{{{NS}}}v")
                        if cached is None:
                            cached = etree.SubElement(cell, f"{{{NS}}}v")
                        if isinstance(value, (datetime, date)):
                            value = to_excel(value)
                        if isinstance(value, bool):
                            cell.set("t", "b")
                            cached.text = "1" if value else "0"
                        elif isinstance(value, (int, float)):
                            cell.set("t", "n")
                            cached.text = str(value)
                        else:
                            cell.set("t", "str")
                            cached.text = str(value)
                    payload = etree.tostring(root, encoding="utf-8")
            target.writestr(item, payload)
    replacement.replace(path)


def _recalc_remote(url: str, input_path: Path, timeout: int = 300) -> bytes:
    """通过远端 LibreOffice 转换服务重算工作簿（POST /，multipart 字段 file，?format=xlsx）。

    该服务默认把上传文件转成 docx，必须带 ?format=xlsx 才返回重算后的 xlsx。
    返回重算后的 xlsx 文件流；任何失败统一抛 ValueError，
    与本地 soffice 失败时保持一致的错误语义。
    """
    if "format=" not in url:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}format=xlsx"
    with open(input_path, "rb") as handle:
        payload = handle.read()
    boundary = "----zqcLibreofficeBoundary7Q2k9"
    body = (
        f"--{boundary}\r\n".encode("utf-8")
        + f'Content-Disposition: form-data; name="file"; filename="{input_path.name}"\r\n'.encode("utf-8")
        + b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n"
        + b"\r\n"
        + payload
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": "zqc-platform/1.0"},
        method="POST",
    )
    # 强制直连，不使用环境里的 http_proxy/https_proxy：该 LibreOffice 服务位于
    # 内网，部署服务器应直连它；测试机上的本地调试代理（如 WorkBuddy 代理）
    # 会误拦对内网 IP 的 POST 请求。
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            data = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:600]
        raise ValueError(f"远端 LibreOffice 转换失败（HTTP {exc.code}）：{detail}")
    except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ValueError(f"远端 LibreOffice 连接失败：{reason}")
    if not data.startswith(b"PK"):
        raise ValueError(f"远端 LibreOffice 返回非 xlsx 内容：{data[:200]!r}")
    return data


def _calculate(template: Path, values: list[tuple[str, int, int, list[list]]],
               work_dir: Path, name: str, soffice: str | None = None,
               libreoffice_url: str | None = None) -> Path:
    source_dir = work_dir / f"{name}-source"
    result_dir = work_dir / f"{name}-calculated"
    source_dir.mkdir()
    result_dir.mkdir()
    book = load_workbook(template)
    for sheet_name, row, col, matrix in values:
        _write(book[sheet_name], row, col, matrix)
    if name == "tracker":
        net = book["按地市纯净收入"]
        month = int(work_dir.name.rsplit("-", 1)[-1])
        for row in range(4, 26):
            for col in range(15, 19):
                net.cell(row, col).value = None
            net[f"O{row}"] = f"=M{row}-C{row}-E{row}-G{row}-I{row}"
            net[f"P{row}"] = f"=N{row}-D{row}-F{row}-H{row}-J{row}"
            net[f"Q{row}"] = f"=VLOOKUP(B{row},'全量客户（客户级）'!$A$2:$BX$27,34,FALSE)/100"
            net[f"R{row}"] = f"=P{row}/{month}*12/O{row}*100%"
    input_path = source_dir / f"{name}.xlsx"
    book.save(input_path)
    book.close()
    if libreoffice_url:
        raw = _recalc_remote(libreoffice_url, input_path)
        output_path = result_dir / input_path.name
        output_path.write_bytes(raw)
        return output_path
    profile = work_dir / f"{name}-lo-profile"
    command = [soffice, f"-env:UserInstallation={profile.as_uri()}", "--headless",
               "--convert-to", "xlsx", "--outdir", str(result_dir), str(input_path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)
    output_path = result_dir / input_path.name
    if result.returncode or not output_path.is_file():
        raise ValueError(f"LibreOffice 模板计算失败：{(result.stderr or result.stdout)[-1000:]}")
    return output_path


def _copy_city_formats(sheet, old_data_row: int, new_data_row: int) -> None:
    old_end = old_data_row + 21
    for block in list(sheet.conditional_formatting):
        if len(block.sqref.ranges) != 1:
            continue
        region = next(iter(block.sqref.ranges))
        if region.min_row < old_data_row or region.max_row > old_end:
            continue
        new_range = (f"{get_column_letter(region.min_col)}{region.min_row + 27}:"
                     f"{get_column_letter(region.max_col)}{region.max_row + 27}")
        for rule in sheet.conditional_formatting[block]:
            sheet.conditional_formatting.add(new_range, copy(rule))


def _fmt(value: float) -> str:
    return f"{float(value):.2f}"


def _trend(value: float) -> str:
    return f"下降{_fmt(-value)}" if value < 0 else f"上升{_fmt(value)}"


def run(config: dict, soffice: str | None = None, libreoffice_url: str | None = None) -> dict:
    libreoffice_url = libreoffice_url or os.environ.get("LIBREOFFICE_URL")
    if libreoffice_url:
        soffice = None  # 远端模式不依赖本地 soffice
    else:
        soffice = soffice or os.environ.get("ZQC_SOFFICE") or shutil.which("soffice")
        if not soffice or not Path(soffice).is_file():
            raise ValueError("缺少 LibreOffice；请设置 LIBREOFFICE_URL（远端转换服务）或 ZQC_SOFFICE（本地 soffice 路径）")
    year, month = int(config["batch"][:4]), int(config["batch"][4:])
    short = f"{year % 100:02d}{month:02d}"
    period_date = date(year, month, 1)
    refs = {key: Path(value) if value else None for key, value in config["refs"].items()}
    sources = {key: Path(value) for key, value in config["sources"].items()}
    positions = config["positions"]
    out_dir = Path(config["outputDir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    source = {}
    for key, path in sources.items():
        book = load_workbook(path, data_only=True, read_only=True)
        source[key] = _values(book.worksheets[0], 9 if key == "business" else 6,
                              30 if key == "business" else 27,
                              1, 292 if key == "business" else 76)
        book.close()
    source_cities = tuple(row[0] for row in source["full"])
    if set(source_cities) != set(CITIES) or len(source_cities) != len(CITIES):
        raise ValueError("客户级源数据缺少全省和 21 个地市")
    for key in ("stock", "grow", "business"):
        if set(row[0] for row in source[key]) != set(source_cities):
            raise ValueError(f"{key} 源数据地市顺序不一致")
    source = {key: sorted(rows, key=lambda row: CITIES.index(row[0]))
              for key, rows in source.items()}

    # Recalculate only the two calculation templates with LibreOffice.
    with tempfile.TemporaryDirectory(prefix=f"monthly-recalc-{month}-") as temporary:
        work = Path(temporary) / f"period-{month}"
        work.mkdir()
        benchmark_calc = _calculate(refs["benchmarkTemplate"], [
            ("拍照客户_客户级", 6, 1, source["full"]),
            ("拍照客户_业务级", 9, 1, source["business"]),
            ("模板", 1, 2, [[month]]),
        ], work, "benchmark", soffice, libreoffice_url)
        tracker_calc = _calculate(refs["trackerTemplate"], [
            ("全量客户（客户级）", 6, 1, source["full"]),
            ("全量客户（业务级）", 9, 1, source["business"]),
            ("存存客户（客户级）", 6, 1, source["stock"]),
            ("存增客户（客户级）", 6, 1, source["grow"]),
        ], work, "tracker", soffice, libreoffice_url)
        benchmark_values = load_workbook(benchmark_calc, data_only=True, read_only=True)
        tracker_values = load_workbook(tracker_calc, data_only=True, read_only=True)

        benchmark_base = refs["benchmarkHistory"] or refs["benchmarkStyle"]
        benchmark = load_workbook(benchmark_base)
        sheet = benchmark.worksheets[0]
        sheet.title = f"{month}月政企收保率对标分析"
        sheet["B1"] = month
        sheet["A2"] = f"{year}年{month}月"
        _write(sheet, 3, 3, _values(benchmark_values["模板"], 3, 24, 3, 67))
        for row in sheet.iter_rows(min_row=3, max_row=24, min_col=3, max_col=67):
            for cell in row:
                cell.number_format = "0.00" if cell.column <= 8 else "0.000"
        benchmark_name = f"{year}年{month}月收保率对标分析表.xlsx"
        benchmark_path = out_dir / benchmark_name
        benchmark.save(benchmark_path)
        benchmark.close()
        _set_formula_cache(benchmark_path, benchmark_base, {})

        history = refs["trackerHistory"]
        tracker = load_workbook(history)
        new_cache: dict[str, dict[str, object]] = {"按地市": {}}
        overall = tracker["整体指标"]
        old_col = positions["overallCol"]
        current_col = old_col + 1
        _copy_range(overall, 2, 2, current_col, current_col, 19, old_col)
        overall.cell(2, current_col).value = period_date
        overall.cell(14, current_col).value = period_date
        computed = _values(tracker_values["总体指标"], 3, 20, 2, 2)
        _write(overall, 3, current_col, computed[:9])
        _write(overall, 15, current_col, computed[12:18])
        overall.column_dimensions[get_column_letter(current_col)].width = 15

        structure = tracker["按用户结构"]
        old_start = positions["structureEnd"] - 5
        new_start = positions["structureEnd"] + 1
        _copy_range(structure, old_start, new_start, 1, 10, 6)
        structure.cell(new_start, 1).value = period_date
        for col in (3, 6, 9):
            structure.cell(new_start + 1, col).value = period_date
        _write(structure, new_start + 2, 2, _values(tracker_values["按用户结构"], 4, 6, 2, 10))
        for region in (f"A{new_start}:A{new_start+1}", f"B{new_start}:D{new_start}",
                       f"E{new_start}:G{new_start}", f"H{new_start}:J{new_start}"):
            structure.merge_cells(region)

        city = tracker["按地市"]
        calculated = _values(tracker_values["按地市"], 4, 25, 1, 36)
        if tuple(row[1] for row in calculated) != CITIES:
            raise ValueError("计算模板地市顺序不符合月报模板")
        old_header = positions["cityEnd"] - 24
        new_header = positions["cityEnd"] + 3
        data_row = new_header + 3
        _copy_range(city, old_header, new_header, 1, 37, 25)
        city.cell(new_header, 1).value = period_date
        _write(city, data_row, 1, calculated)
        city_merges = (
            (1, 2, 2), (3, 11, 0), (12, 20, 0), (21, 22, 0), (23, 36, 0), (37, 37, 2),
        )
        for first, last, row_span in city_merges:
            city.merge_cells(start_row=new_header, end_row=new_header + row_span,
                             start_column=first, end_column=last)
        for first, last in ((3, 5), (6, 8), (9, 11), (12, 14), (15, 17), (18, 20),
                            (21, 21), (22, 22), (23, 27), (28, 32), (33, 36)):
            city.merge_cells(start_row=new_header + 1,
                             end_row=new_header + (2 if first in (21, 22) else 1),
                             start_column=first, end_column=last)
        old_data_row = positions["cityEnd"] - 21
        for row in range(data_row, data_row + 22):
            previous = row - 27
            for destination, input_col in ((25, 24), (27, 26), (30, 29), (32, 31), (35, 34)):
                cell = city.cell(row, destination)
                current = city.cell(row, input_col).value
                baseline = city.cell(previous, input_col).value
                cell.value = f"={get_column_letter(input_col)}{row}-{get_column_letter(input_col)}{previous}"
                new_cache["按地市"][cell.coordinate] = current - baseline
            rank = city.cell(row, 37)
            if row == data_row:
                rank.value = "—"
            else:
                rank.value = f"=RANK(L{row},$L${data_row+1}:$L${data_row+21},0)"
                current = city.cell(row, 12).value
                new_cache["按地市"][rank.coordinate] = 1 + sum(
                    city.cell(other, 12).value > current
                    for other in range(data_row + 1, data_row + 22))
        _copy_city_formats(city, old_data_row, data_row)

        net = tracker["按地市纯净收入"]
        net["A1"] = f"{month}月"
        _write(net, 4, 3, _values(tracker_values["按地市纯净收入"], 4, 25, 13, 18))

        for name in ("按产品-客保", "按产品-收保"):
            product = tracker[name]
            old_col = positions["productCol"]
            current_col = old_col + 1
            _copy_range(product, 2, 2, current_col, current_col, 28, old_col)
            values = _values(tracker_values[name], 2, 29, 2, 2)
            for row in (2, 8, 14, 20, 26):
                values[row - 2][0] = int(short)
            _write(product, 2, current_col, values)

        composition = tracker["各产品下用户结构"]
        old_col = positions["compositionCol"] - 3
        new_col = positions["compositionCol"] + 1
        _copy_range(composition, 1, 1, new_col, new_col + 3, 20, old_col)
        values = _values(tracker_values["各产品下用户结构"], 1, 20, 13, 16)
        for row in (1, 5, 9, 13, 17):
            values[row - 1][0] = f"{year % 100:02d}年{month}月"
            values[row - 1][2] = f"{year % 100:02d}年{month}月结构"
        _write(composition, 1, new_col, values)
        for row in (1, 5, 9, 13, 17):
            composition.merge_cells(start_row=row, end_row=row, start_column=new_col + 2,
                                    end_column=new_col + 3)
            for col in (new_col, new_col + 1):
                composition.merge_cells(start_row=row + 1, end_row=row + 3,
                                        start_column=col, end_column=col)
        for name, count in (("整体指标", 21), ("按地市", 18), ("按产品-客保", 15),
                            ("按产品-收保", 16), ("各产品下用户结构", 122)):
            tracker[name].freeze_panes = f"{get_column_letter(count+1)}1"
        tracker_name = f"政企存量客户指标跟踪-{short}.xlsx"
        tracker_path = out_dir / tracker_name
        tracker.save(tracker_path)
        tracker.close()
        _set_formula_cache(tracker_path, history, new_cache)

        notice_base = refs["noticeHistory"] or refs["noticeStyle"]
        notice = load_workbook(notice_base)
        notice_sheet = notice.worksheets[0]
        notice_sheet["C2"] = f"收保客保月报（政企客户）-{month}月"
        notice_sheet["A4"] = f"{month}月"
        city_data = _values(load_workbook(tracker_path, data_only=True)["按地市"],
                            data_row, data_row + 21, 1, 37)
        _write(notice_sheet, 7, 1, city_data)
        notice_name = f"{year}年{month}月政企存量客户通报.xlsx"
        notice_path = out_dir / notice_name
        notice.save(notice_path)
        notice.close()
        _set_formula_cache(notice_path, notice_base, {})
        benchmark_values.close()
        tracker_values.close()

    province = city_data[0]
    local = [dict(city=row[1], customer=row[2], customer_yoy=row[3], customer_mom=row[4],
                  revenue=row[11], revenue_yoy=row[12], revenue_mom=row[13])
             for row in city_data[1:]]
    customer_bottom = sorted(local, key=lambda item: item["customer"])[:5]
    revenue_bottom = sorted(local, key=lambda item: item["revenue"])[:5]
    low = lambda rows, key: min(rows, key=lambda item: item[key])
    cy = low(customer_bottom, "customer_yoy")
    cm = low(customer_bottom, "customer_mom")
    ry = low(revenue_bottom, "revenue_yoy")
    rm = low(revenue_bottom, "revenue_mom")
    text = "\n".join((
        f"{year}年{month}月份全省各地市存量经营指标通报数据见附件，请惠存。", "",
        f"{month}月全省客保率{_fmt(province[2])}%，环比{_trend(province[4])}个百分点；收保率{_fmt(province[11])}%，环比{_trend(province[13])}个百分点。", "",
        f"客保率后五为{'、'.join(item['city'] for item in customer_bottom)}。其中，{cy['city']}同比{_trend(cy['customer_yoy'])}个百分点，{cm['city']}环比{_trend(cm['customer_mom'])}个百分点，请重点关注。", "",
        f"收保率后五为{'、'.join(item['city'] for item in revenue_bottom)}。其中，{ry['city']}同比{_trend(ry['revenue_yoy'])}个百分点，{rm['city']}环比{_trend(rm['revenue_mom'])}个百分点，请重点关注。", "",
    ))
    text_name = f"通报文案-{year}年{month}月.txt"
    (out_dir / text_name).write_text(text, encoding="utf-8")
    return {"outputs": [benchmark_name, tracker_name, notice_name, text_name],
            "customerBottom": [item["city"] for item in customer_bottom],
            "revenueBottom": [item["city"] for item in revenue_bottom]}


if __name__ == "__main__":
    import sys
    print(json.dumps(run(json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))), ensure_ascii=False))
