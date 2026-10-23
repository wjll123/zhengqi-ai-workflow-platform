"""名单读取与企业标识核对，不从其他业务数据补造企业。"""
import csv
import re
from pathlib import Path

from openpyxl import load_workbook

from .errors import QueryError

NAME_HEADERS = ("公司名称", "企业名称", "最新企业名称", "客户名称", "单位名称", "公司名", "企业名", "名称")


def normalize_name(value):
    return re.sub(r"\s+", "", str(value or "")).translate(str.maketrans("（）", "()"))


def _path(name, root):
    if not isinstance(name, str) or not name or Path(name).name != name:
        raise QueryError("请选择已上传的文件。")
    path = (Path(root) / name).resolve()
    if not path.is_relative_to(Path(root).resolve()) or not path.is_file():
        raise QueryError("文件不存在，请重新上传。")
    return path


def _rows(path):
    try:
        if path.suffix.lower() == ".xlsx":
            book = load_workbook(path, read_only=True, data_only=True)
            try:
                return list(book.active.values)
            finally:
                book.close()
        if path.suffix.lower() == ".xls":
            import xlrd
            book = xlrd.open_workbook(path)
            try:
                sheet = book.sheet_by_index(0)
                return [sheet.row_values(i) for i in range(sheet.nrows)]
            finally:
                book.release_resources()
        if path.suffix.lower() == ".csv":
            for encoding in ("utf-8-sig", "gb18030"):
                try:
                    with path.open(encoding=encoding, newline="") as stream:
                        return list(csv.reader(stream))
                except UnicodeDecodeError:
                    continue
        raise QueryError("名单仅支持 xlsx、xls、csv。")
    except QueryError:
        raise
    except Exception as exc:
        raise QueryError("文件无法读取，请确认表格未损坏且没有加密。") from exc


def _value(row, column):
    return str(row[column] or "").strip() if column < len(row) else ""


def read_names(name, root):
    rows = _rows(_path(name, root))
    header, column = -1, None
    for index, row in enumerate(rows[:10]):
        for col, value in enumerate(row):
            if normalize_name(value) in NAME_HEADERS:
                header, column = index, col
                break
        if column is not None:
            break
    if column is None:
        populated = [row for row in rows if any(_value(row, i) for i in range(len(row)))]
        if not populated or any(sum(bool(_value(row, i)) for i in range(len(row))) > 1 for row in populated):
            raise QueryError("请提供“公司名称”列，或仅含一列公司全称的名单。")
        column = next(i for i in range(len(populated[0])) if _value(populated[0], i))
    selected = rows[header + 1:]
    # 支持现有业务清单：A/B 同名为已有项，仅 B 列有值为新增项。
    if header >= 0 and column == 1 and not _value(rows[header], 0):
        baseline = [row for row in selected if _value(row, 0)]
        additions = [row for row in selected if not _value(row, 0) and _value(row, 1)]
        if (baseline and additions
                and all(normalize_name(_value(r, 0)) == normalize_name(_value(r, 1)) for r in baseline)
                and all(not any(_value(r, c) for c in range(2, len(r))) for r in additions)):
            selected = additions
    names, seen = [], set()
    for row in selected:
        value = _value(row, column)
        if not value:
            continue
        key = normalize_name(value)
        if not 2 <= len(key) <= 120 or value.startswith(("=", "+", "-", "@")):
            raise QueryError("名单存在无效企业名称，请检查名称长度和公式单元格。")
        if key not in seen:
            names.append(value)
            seen.add(key)
    if not names:
        raise QueryError("名单中没有可查询的企业。")
    if len(names) > 100:
        raise QueryError(f"名单去重后有 {len(names)} 家，每份最多 100 家，请拆分后上传。")
    return names


def read_codes(name, names, root):
    rows = _rows(_path(name, root))
    wanted = {normalize_name(item): item for item in names}
    for offset, row in enumerate(rows[:10]):
        headers = [normalize_name(value) for value in row]
        code_column = next((i for i, value in enumerate(headers) if value in ("统一社会信用代码", "信用代码")), None)
        name_column = next((headers.index(key) for key in ("最新企业名称", *NAME_HEADERS) if key in headers), None)
        if code_column is not None and name_column is not None:
            break
    else:
        raise QueryError("辅助表需包含企业名称、统一社会信用代码两列。")
    found = {}
    for row in rows[offset + 1:]:
        name_key = normalize_name(_value(row, name_column))
        if name_key not in wanted:
            continue
        code = _value(row, code_column).upper()
        if not code or code == "-":
            continue
        if not re.fullmatch(r"[0-9A-Z]{18}", code):
            raise QueryError(f"{wanted[name_key]} 的统一社会信用代码格式不正确。")
        if name_key in found and found[name_key] != code:
            raise QueryError(f"{wanted[name_key]} 对应多个信用代码，请先核对辅助表。")
        found[name_key] = code
    return {wanted[key]: code for key, code in found.items()}
