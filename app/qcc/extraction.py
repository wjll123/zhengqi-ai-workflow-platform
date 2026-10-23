"""解析浏览器已加载的企查查页面，严格核对企业身份与记录总数。"""
import html
import json
import math
import re
from urllib.parse import urlsplit

from .errors import BrowserActionRequired, CompanyNotFound, PageNotReady, QueryError
from .sources import normalize_name
from .fields import (SCHEMA_VERSION, FIELD_SCOPE, SHARE_FIELDS, PEOPLE_FIELDS,
                     mask_personal_ids, project, contributions, supplement_note)

STATE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*({)")
FIRM_KEY = re.compile(r"(?:[0-9a-f]{32}|g[0-9a-f]{32})", re.I)


def listed_name_matches(company, name):
    wanted = normalize_name(name)
    if normalize_name(company.get("Name", "")) == wanted:
        return True
    originals = company.get("OriginalName") or []
    if not isinstance(originals, list):
        return False
    return any(normalize_name(item.get("Name") or item.get("text") or "") == wanted
               for item in originals if isinstance(item, dict))


def page_state(source, url=""):
    parsed = urlsplit(url)
    title = re.search(r"<title[^>]*>(.*?)</title>", source, re.S | re.I)
    title = html.unescape(title.group(1)) if title else ""
    if any(word in title for word in ("用户验证", "安全验证", "访问受限", "访问异常", "操作频繁")):
        raise BrowserActionRequired("企查查要求验证。请在当前查询浏览器窗口完成验证后继续。")
    if "login" in parsed.path.lower() or "登录" in title:
        raise BrowserActionRequired("企查查要求登录。请在当前查询浏览器窗口登录后继续。")
    if parsed.hostname and parsed.hostname not in {"www.qcc.com", "m.qcc.com", "qcc.com"}:
        raise BrowserActionRequired("页面已跳转离开企查查国内站，请在当前查询浏览器检查页面后继续。")
    match = STATE.search(source)
    if not match:
        raise PageNotReady("页面还未提供企业资料。")
    try:
        state, _ = json.JSONDecoder().raw_decode(source[match.start(1):])
    except ValueError as exc:
        raise PageNotReady("页面资料仍未完整加载。") from exc
    if not isinstance(state, dict):
        raise QueryError("页面资料格式已变化，需要更新解析器。")
    return state


def search_target(source, name, code="", url="", *, by_code=False):
    if by_code and not code:
        raise QueryError("按信用代码匹配时必须提供信用代码。")
    state = page_state(source, url)
    try:
        result = state["search"]["searchRes"]
        rows = result["Result"]
    except (KeyError, TypeError) as exc:
        raise PageNotReady("搜索结果尚未加载。") from exc
    if result.get("Status") != 200 or not isinstance(rows, list):
        raise BrowserActionRequired("搜索未正常返回，请在当前查询浏览器检查登录或验证提示。")
    matches = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate = normalize_name(html.unescape(re.sub(r"<[^>]+>", "", str(row.get("Name") or ""))))
        identity = str(row.get("CreditCode") or "").upper()
        key = str(row.get("KeyNo") or "")
        if (candidate == normalize_name(name) or by_code) and (not code or identity == code.upper()):
            if FIRM_KEY.fullmatch(key):
                matches.add("https://m.qcc.com/firm/" + key.lower() + ".html")
    if len(matches) > 1:
        raise QueryError("存在多个同名企业，请提供统一社会信用代码辅助表后重新查询。")
    if not matches:
        raise CompanyNotFound("未找到与所提供信用代码一致的企业，请核对原始名单。" if by_code else "未找到名称及所提供信用代码一致的企业，请核对名单。")
    return matches.pop()


def company_data(source, name, code="", url="", *, by_code=False):
    if by_code and not code:
        raise QueryError("按信用代码核对时必须提供信用代码。")
    state = page_state(source, url)
    company = (state.get("company") or {}).get("companyDetail")
    if not isinstance(company, dict) or not company.get("Name"):
        raise PageNotReady("企业详情尚未加载。")
    name_changed = normalize_name(company["Name"]) != normalize_name(name)
    original_name_match = name_changed and listed_name_matches(company, name)
    if name_changed and not by_code and not original_name_match:
        raise QueryError("详情页企业名称与名单不一致，已拒绝采集。")
    actual_code = str(company.get("CreditCode") or "").upper()
    if code and actual_code != code.upper():
        raise QueryError("详情页统一社会信用代码与名单不一致，已拒绝采集。")
    key = str(company.get("KeyNo") or company.get("_id") or "")
    if FIRM_KEY.fullmatch(key) and urlsplit(url).path != f"/firm/{key.lower()}.html":
        raise QueryError("企业详情链接与页面企业标识不一致。")
    counts = company.get("CountInfo") or {}
    for field, label, count_key in (("Partners", "股东", "PartnersCount"), ("Employees", "主要人员", "EmployeesCount")):
        rows, count = company.get(field), counts.get(count_key)
        if count is None and field == "Employees":
            count = company.get("EmployeesCount")
        if isinstance(count, str) and count.isdigit():
            count = int(count)
        if not isinstance(rows, list):
            if count == 0:
                company[field] = []
                continue
            raise QueryError(f"页面未提供{label}数据，不能作为零条记录。")
        if type(count) is not int or count < 0:
            raise QueryError(f"页面未提供可靠的{label}总数，无法确认数据完整性。")
        if len(rows) != count:
            raise QueryError(f"{label}应有 {count} 条，页面只返回 {len(rows)} 条；请使用官方导出取得完整资料。")
    shareholders = []
    for row in company["Partners"]:
        if not isinstance(row, dict):
            raise QueryError("股东记录格式异常。")
        shareholder = str(row.get("StockName") or "").strip()
        raw_percent = row.get("StockPercentValue")
        if not shareholder or raw_percent is None or isinstance(raw_percent, bool):
            raise QueryError("股东名称或持股比例缺失，未将不完整记录标记为成功。")
        try:
            percent = float(raw_percent)
        except (TypeError, ValueError) as exc:
            raise QueryError("股东持股比例格式异常。") from exc
        if not math.isfinite(percent) or not 0 <= percent <= 100:
            raise QueryError("股东持股比例超出有效范围。")
        for key in ("PartnerShouldDetailList", "PartnerRealDetailList"):
            details = row.get(key)
            if details is not None and (not isinstance(details, list) or any(not isinstance(item, dict) for item in details)):
                raise QueryError("股东分期出资明细格式异常，未将不完整资料标记为成功。")
        shareholders.append(mask_personal_ids(row))
    people = []
    for row in company["Employees"]:
        if not isinstance(row, dict) or not row.get("Name") or not row.get("Job"):
            raise QueryError("主要人员姓名或职务缺失。")
        people.append(mask_personal_ids(row, person=True))
    extra = [supplement_note(row) for row in shareholders + people]
    extra = list(dict.fromkeys(note for note in extra if note != "无额外详情提示"))
    return {"company": company["Name"], "credit_code": actual_code, "url": url,
            "schema_version": SCHEMA_VERSION, "field_scope": FIELD_SCOPE,
            "coverage": {"shareholders": {"expected": len(shareholders), "captured": len(shareholders)},
                         "people": {"expected": len(people), "captured": len(people)}},
            "additional_details": "；".join(extra),
            "raw_records": {"shareholders": shareholders, "people": people},
            "match_note": (f"企查查原名称与名单一致；现登记名称：{company['Name']}。原始名单名称已保留。"
                           if original_name_match else
                           f"信用代码一致；企查查登记名称：{company['Name']}。原始名单名称已保留。"
                           if name_changed else ""),
            "shareholders": project(shareholders, SHARE_FIELDS),
            "people": project(people, PEOPLE_FIELDS), "contributions": contributions(shareholders)}
