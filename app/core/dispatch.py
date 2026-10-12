"""离线派单工作台：活动准备、人工核对和外部执行记录。

这里不调用智慧中台。外部状态由操作人录入，并保留本地审计记录。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from .config import STATE_DIR

DB_PATH = STATE_DIR / "dispatch.sqlite3"

WORKFLOWS = {
    "customer": {
        "name": "双线质差 · 直销客户级",
        "insight": "直销客户",
        "planning_type": "政企直销客户派单",
        "methods": ["按责任客户经理", "到认领包区（直销）", "按机构"],
        "steps": [
            "登录智慧营销中台，选择营销策划岗；进入客户洞察 → 自助洞察 → 直销客户。",
            "按客户级条件试算；如需用户级号码清单，同时设置至少一个客户级限制条件。",
            "保存目标客户群，并等待客户群管理显示保存完成。",
            "新建政企活动，策划类型选政企直销客户派单，触发类型选批量营销。",
            "配置评价规则、方案概述、全局话术和最多 3 个附件。",
            "渠道选统一商机库（省841），移除工单池（翼门户/企微），取消系统自带规则校验。",
            "核对客群数量和派单方式；按机构派发时选定下发组织。",
            "暂存 → 送审；审批通过后执行，并点击任务数刷新。",
            "在批量派单查询及 841/机会池核对结果，再回到本平台登记。",
        ],
    },
    "number": {
        "name": "双线质差 · 政企用户号码级",
        "insight": "专线",
        "planning_type": "政企用户派单",
        "methods": ["按责任客户经理", "按机构"],
        "steps": [
            "登录智慧营销中台，选择营销策划岗；进入客户洞察 → 自助洞察 → 专线。",
            "设置专线请求与限制标签，试算客户群并保存。",
            "新建政企活动，策划类型选政企用户派单，触发类型选批量营销。",
            "按业务指引配置标签表达式评价规则；指引示例为直销客户名称=1111111，使用前由业务方复核适用性。",
            "配置目标客户群、方案概述、全局话术和最多 3 个附件。",
            "渠道选统一商机库（省841），移除工单池（翼门户/企微），取消系统自带规则校验。",
            "核对客群数量和派单方式；按机构派发时选定下发组织。",
            "暂存 → 送审；审批通过后执行，并点击任务数刷新。",
            "在批量派单查询及 841/机会池核对结果，再回到本平台登记。",
        ],
    },
}

CHECKS = [
    ("M1", "渠道为统一商机库（省841）"),
    ("M2", "已删除工单池（翼门户/企微）"),
    ("M3", "已取消系统自带规则校验"),
    ("M4", "评价规则已按本活动业务口径复核"),
    ("M5", "目标客群数据总量与试算结果一致"),
    ("M6", "用户级试算已设置至少一个客户级限制条件"),
    ("M7", "客户级客户群保存前已删除用户级标签"),
    ("M8", "执行后已点击任务数刷新"),
]

NEXT_STATUS = {
    "draft": "prepared",
    "prepared": "submitted",
    "submitted": "approved",
    "approved": "executed",
    "executed": "refreshed",
    "refreshed": "verified",
}


class DispatchError(ValueError):
    pass


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("""CREATE TABLE IF NOT EXISTS plans (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        workflow TEXT NOT NULL, activity_name TEXT NOT NULL, group_name TEXT NOT NULL,
        audience_level TEXT NOT NULL, method TEXT NOT NULL, expected_count INTEGER NOT NULL,
        organization TEXT NOT NULL DEFAULT '', summary TEXT NOT NULL DEFAULT '',
        script TEXT NOT NULL DEFAULT '', operator TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft', external_activity_id TEXT NOT NULL DEFAULT '',
        dispatched_count INTEGER, note TEXT NOT NULL DEFAULT ''
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS checks (
        plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
        code TEXT NOT NULL, checked INTEGER NOT NULL, evidence TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL, PRIMARY KEY(plan_id, code)
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE CASCADE,
        at TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL
    )""")
    return conn


def _required_checks(plan: dict) -> list[str]:
    codes = ["M1", "M2", "M3", "M4", "M5"]
    if plan["workflow"] == "customer":
        codes.append("M6" if plan["audience_level"] == "user" else "M7")
    return codes


def _clean_text(data: dict, key: str, limit: int, required: bool = False) -> str:
    value = str(data.get(key, "") or "").strip()
    if required and not value:
        raise DispatchError(f"{key} 不能为空")
    if len(value) > limit:
        raise DispatchError(f"{key} 不能超过 {limit} 字")
    return value


def create_plan(data: dict, path: Path = DB_PATH) -> dict:
    workflow = _clean_text(data, "workflow", 20, True)
    if workflow not in WORKFLOWS:
        raise DispatchError("未知派单流程")
    audience = _clean_text(data, "audience_level", 20, True)
    if audience not in ("customer", "user") or (workflow == "number" and audience != "customer"):
        raise DispatchError("当前流程不支持该客群层级")
    method = _clean_text(data, "method", 40, True)
    if method not in WORKFLOWS[workflow]["methods"]:
        raise DispatchError("当前流程不支持该派单方式")
    try:
        count = int(data.get("expected_count"))
    except (TypeError, ValueError):
        raise DispatchError("试算客群数量必须为整数") from None
    if count <= 0 or count > 100_000_000:
        raise DispatchError("试算客群数量必须大于 0")
    organization = _clean_text(data, "organization", 200)
    if method == "按机构" and not organization:
        raise DispatchError("按机构派发时必须填写下发组织")
    values = {
        "id": uuid.uuid4().hex,
        "created_at": _now(),
        "updated_at": _now(),
        "workflow": workflow,
        "activity_name": _clean_text(data, "activity_name", 160, True),
        "group_name": _clean_text(data, "group_name", 160, True),
        "audience_level": audience,
        "method": method,
        "expected_count": count,
        "organization": organization,
        "summary": _clean_text(data, "summary", 2000),
        "script": _clean_text(data, "script", 4000),
        "operator": _clean_text(data, "operator", 100, True),
    }
    with _connect(path) as conn:
        conn.execute("""INSERT INTO plans
            (id, created_at, updated_at, workflow, activity_name, group_name,
             audience_level, method, expected_count, organization, summary, script, operator)
            VALUES (:id,:created_at,:updated_at,:workflow,:activity_name,:group_name,
                    :audience_level,:method,:expected_count,:organization,:summary,:script,:operator)""", values)
        conn.execute("INSERT INTO events(plan_id,at,action,detail) VALUES(?,?,?,?)",
                     (values["id"], _now(), "created", "创建本地派单方案"))
    return get_plan(values["id"], path)


def get_plan(plan_id: str, path: Path = DB_PATH) -> dict | None:
    with _connect(path) as conn:
        row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
        if row is None:
            return None
        plan = dict(row)
        found = {r["code"]: dict(r) for r in conn.execute(
            "SELECT * FROM checks WHERE plan_id=?", (plan_id,))}
        plan["checks"] = [
            {"code": code, "label": label, "required": code in _required_checks(plan),
             "checked": bool(found.get(code, {}).get("checked")),
             "evidence": found.get(code, {}).get("evidence", "")}
            for code, label in CHECKS
        ]
        plan["events"] = [dict(r) for r in conn.execute(
            "SELECT at,action,detail FROM events WHERE plan_id=? ORDER BY id", (plan_id,))]
        plan["workflow_info"] = WORKFLOWS[plan["workflow"]]
        plan["next_status"] = NEXT_STATUS.get(plan["status"])
        return plan


def list_plans(path: Path = DB_PATH) -> list[dict]:
    with _connect(path) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id,created_at,updated_at,workflow,activity_name,method,status,operator "
            "FROM plans ORDER BY created_at DESC, rowid DESC")]


def begin_rpa(plan_id: str, stage: str, path: Path = DB_PATH) -> None:
    """每个方案每个阶段只允许一次自动尝试，避免失败后盲目重复派单。"""
    with _connect(path) as conn:
        if conn.execute("SELECT 1 FROM events WHERE plan_id=? AND action=?",
                        (plan_id, f"rpa_start_{stage}")).fetchone():
            raise DispatchError("该阶段已尝试自动执行；请先核对中台状态，避免重复派单")
        conn.execute("INSERT INTO events(plan_id,at,action,detail) VALUES(?,?,?,?)",
                     (plan_id, _now(), f"rpa_start_{stage}", "开始本机浏览器自动操作"))


def finish_rpa(plan_id: str, stage: str, ok: bool, detail: str,
               path: Path = DB_PATH) -> None:
    with _connect(path) as conn:
        conn.execute("INSERT INTO events(plan_id,at,action,detail) VALUES(?,?,?,?)",
                     (plan_id, _now(), f"rpa_{'done' if ok else 'failed'}_{stage}",
                      str(detail)[:500]))


def set_check(plan_id: str, code: str, checked: bool, evidence: str = "",
              path: Path = DB_PATH) -> dict:
    plan = get_plan(plan_id, path)
    if plan is None:
        raise DispatchError("派单方案不存在")
    if code not in dict(CHECKS):
        raise DispatchError("未知核对项")
    if code == "M8" and plan["status"] != "executed":
        raise DispatchError("M8 只能在外部系统执行后确认")
    if code != "M8" and plan["status"] != "draft":
        raise DispatchError("预检项只能在准备阶段修改")
    evidence = str(evidence or "").strip()
    if len(evidence) > 500:
        raise DispatchError("核对说明不能超过 500 字")
    if code == "M5" and checked:
        try:
            saved_count = int(evidence)
        except ValueError:
            raise DispatchError("M5 请在核对依据填写中台显示的目标客群数量") from None
        if saved_count != plan["expected_count"]:
            raise DispatchError("M5 目标客群数量与试算数量不一致")
    with _connect(path) as conn:
        conn.execute("""INSERT INTO checks(plan_id,code,checked,evidence,updated_at)
            VALUES(?,?,?,?,?) ON CONFLICT(plan_id,code) DO UPDATE SET
            checked=excluded.checked,evidence=excluded.evidence,updated_at=excluded.updated_at""",
            (plan_id, code, int(bool(checked)), evidence, _now()))
        conn.execute("INSERT INTO events(plan_id,at,action,detail) VALUES(?,?,?,?)",
                     (plan_id, _now(), "check", f"{code}：{'确认' if checked else '取消'}"))
    return get_plan(plan_id, path)


def advance_plan(plan_id: str, target: str, data: dict, path: Path = DB_PATH) -> dict:
    plan = get_plan(plan_id, path)
    if plan is None:
        raise DispatchError("派单方案不存在")
    if NEXT_STATUS.get(plan["status"]) != target:
        raise DispatchError("只能按派单流程顺序推进")
    updates: dict[str, object] = {"status": target, "updated_at": _now()}
    if target == "prepared":
        missing = [c["code"] for c in plan["checks"] if c["required"] and not c["checked"]]
        if missing:
            raise DispatchError("尚未确认必做项：" + "、".join(missing))
    if target == "submitted":
        updates["external_activity_id"] = _clean_text(data, "external_activity_id", 160, True)
    if target == "refreshed":
        if not any(c["code"] == "M8" and c["checked"] for c in plan["checks"]):
            raise DispatchError("请先确认 M8：已点击任务数刷新")
    if target == "verified":
        try:
            count = int(data.get("dispatched_count"))
        except (TypeError, ValueError):
            raise DispatchError("请填写外部系统查询到的实际派单数") from None
        if count < 0 or count > 100_000_000:
            raise DispatchError("实际派单数无效")
        updates["dispatched_count"] = count
        updates["note"] = _clean_text(data, "note", 1000, count != plan["expected_count"])
    with _connect(path) as conn:
        fields = ",".join(f"{key}=?" for key in updates)
        changed = conn.execute(f"UPDATE plans SET {fields} WHERE id=? AND status=?",
                               (*updates.values(), plan_id, plan["status"]))
        if changed.rowcount != 1:
            raise DispatchError("方案状态已变化，请刷新后重试")
        conn.execute("INSERT INTO events(plan_id,at,action,detail) VALUES(?,?,?,?)",
                     (plan_id, _now(), target, f"人工登记外部状态：{target}"))
    return get_plan(plan_id, path)


def action_card(plan: dict) -> str:
    info = plan["workflow_info"]
    lines = [
        "政企存量平台 · 派单操作卡", "=" * 32,
        f"活动：{plan['activity_name']}", f"流程：{info['name']}",
        f"洞察板块：{info['insight']}", f"策划类型：{info['planning_type']}",
        f"客户群：{plan['group_name']}（试算 {plan['expected_count']}）",
        f"客群层级：{'用户级' if plan['audience_level'] == 'user' else '客户级'}",
        f"派单方式：{plan['method']}", f"下发组织：{plan['organization'] or '—'}",
        f"操作人：{plan['operator']}",
        f"方案概述：{plan['summary'] or '待填写'}",
        f"全局话术：{plan['script'] or '待填写'}", "",
        "操作顺序：",
        *[f"{i}. {step}" for i, step in enumerate(info["steps"], 1)],
        "", "必做核对：",
        *[f"[{'√' if c['checked'] else ' '}] {c['code']} {c['label']}"
          for c in plan["checks"] if c["required"] or c["code"] == "M8"],
        "", "外部系统活动号：" + (plan["external_activity_id"] or "待填写"),
        "实际派单数：" + (str(plan["dispatched_count"]) if plan["dispatched_count"] is not None else "待核对"),
        "说明：本卡只指导操作和记录，不能代表智慧中台已执行派单。",
    ]
    return "\n".join(lines) + "\n"
