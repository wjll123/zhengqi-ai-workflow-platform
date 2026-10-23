"""企业详情页的完整业务字段；原始记录另外留存，便于复核。"""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

SCHEMA_VERSION = 2
FIELD_SCOPE = "企业详情页披露字段"
MISSING = "未披露"

SHARE_FIELDS = [
    ("StockName", "股东名称"), ("StockType", "股东类型"), ("StockPercentValue", "持股比例"),
    ("ShouldCapi", "认缴出资（万元）"), ("RealCapi", "实缴出资（万元）"),
    ("ShoudDate", "认缴出资日期"), ("CapiDate", "实缴出资日期"),
    ("InvestType", "认缴出资方式"), ("InvestName", "实缴出资方式"),
    ("TotalShouldAmount", "认缴合计（万元）"), ("TotalRealAmount", "实缴合计（万元）"),
    ("InDate", "入股日期"), ("Tags", "股东标签"),
    ("FinalBenefitPercent", "最终受益比例"), ("IndirectStockPercent", "间接持股比例"),
    ("CompanyCount", "关联企业数量"), ("Job", "在本企业职务"),
    ("IdentifyType", "证照类型"), ("IdentifyNo", "证照号码（个人脱敏）"),
    ("HasStockFreezenInfo", "有股权冻结信息"), ("HasPledgeInfo", "有股权出质信息"),
    ("StockPercent", "页面持股比例原文"), ("TagConfig", "标签补充信息"),
]
PEOPLE_FIELDS = [
    ("Name", "姓名"), ("Job", "职务"), ("CompanyCount", "关联企业数量"),
    ("Tags", "人员标签"), ("FinalBenefitPercent", "最终受益比例"),
    ("StockPercent", "持股比例"), ("InDate", "任职日期"),
    ("ScertName", "证件类型"), ("CerNo", "证件号码（脱敏）"),
]
DETAIL_HEADERS = ["股东名称", "出资类别", "期次", "出资金额（万元）", "出资日期", "出资方式", "其他披露字段"]
INTERNAL = {"KeyNo", "Org", "No", "HasImage", "ImageUrl", "StockTypeCode", "SpecialTypeCode", "JobCode"}
NESTED = {"PartnerShouldDetailList", "PartnerRealDetailList"}


def mask_personal_ids(row, *, person=False):
    result = json.loads(json.dumps(row, ensure_ascii=False))
    natural = person or "自然人" in str(row.get("StockType", "")) or "身份证" in str(row.get("IdentifyType", ""))
    for key in ("CerNo", "IdentifyNo"):
        value = result.get(key)
        if value and (key == "CerNo" or natural):
            value = str(value)
            if not any(mark in value for mark in ("*", "＊", "•")):
                result[key] = value[:3] + "********" + value[-4:] if len(value) > 8 else "已脱敏"
    return result


def display(value):
    if value is None or value == "" or value == [] or value == {}:
        return MISSING
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list)):
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return "、".join(value)
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def date_value(value):
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000 if value > 10**11 else value,
                                          ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            pass
    return display(value)


def supplement_note(row):
    notes = []
    for key, label in (("HasIntro", "人物简介"), ("HasStockDetail", "持股详情")):
        if row.get(key) in (True, 1, "1"):
            notes.append(label + "有入口，详情正文未包含在本页")
    return "；".join(notes) or "无额外详情提示"


def project(rows, fields):
    known = {key for key, _ in fields} | INTERNAL | NESTED | {"HasIntro", "HasStockDetail"}
    # 未识别的新增业务字段保留，避免页面增加字段时静默丢弃。
    extra = sorted({key for row in rows for key in row if key not in known})
    headers = [label for _, label in fields] + ["其他披露字段·" + key for key in extra] + ["补充详情提示"]
    projected = []
    for row in rows:
        values = []
        for key, _ in fields:
            value = row.get(key)
            if key == "StockPercentValue" and value is not None:
                value = float(value) / 100
            values.append(date_value(value) if key == "InDate" else display(value))
        projected.append(values + [display(row.get(key)) for key in extra] + [supplement_note(row)])
    return {"headers": headers, "rows": projected}


def contributions(rows):
    result = []
    for row in rows:
        for key, label, amount, date, method in (
                ("PartnerShouldDetailList", "认缴", "ShouldCapi", "ShoudDate", "InvestType"),
                ("PartnerRealDetailList", "实缴", "RealCapi", "CapiDate", "InvestName")):
            for index, detail in enumerate(row.get(key) or [], 1):
                other = {k: v for k, v in detail.items() if k not in {amount, date, method}}
                result.append([row["StockName"], label, index, display(detail.get(amount)),
                               display(detail.get(date)), display(detail.get(method)), display(other)])
    return {"headers": DETAIL_HEADERS, "rows": result}


def is_full(data):
    return isinstance(data, dict) and data.get("schema_version") == SCHEMA_VERSION
