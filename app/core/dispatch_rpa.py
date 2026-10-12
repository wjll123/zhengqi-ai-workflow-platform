"""本机浏览器 RPA 执行器。

只连接 127.0.0.1 的 Chrome/Edge 调试端口；页面选择器由内网联调后配置。
没有经过校准的配置时拒绝运行，避免误点真实派单按钮。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import browser
from .config import CONFIG_DIR

CONFIG_FILE = CONFIG_DIR / "dispatch_rpa.json"
STAGES = ("prepare_submit", "execute_refresh")
ALLOWED_ACTIONS = {"click", "fill", "select", "wait_visible", "assert_text"}
ALLOWED_FIELDS = {"activity_name", "group_name", "method", "organization",
                  "summary", "script", "external_activity_id"}


class RPAError(RuntimeError):
    pass


def load_config(path: Path = CONFIG_FILE) -> dict:
    if not path.exists():
        raise RPAError("缺少派单 RPA 配置文件")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RPAError(f"派单 RPA 配置无法读取：{exc}") from exc
    if not isinstance(config, dict) or config.get("version") != 1:
        raise RPAError("派单 RPA 配置版本无效")
    return config


def _stage_steps(config: dict, workflow: str, stage: str) -> list[dict]:
    if workflow not in ("customer", "number") or stage not in STAGES:
        raise RPAError("未知派单工作流或执行阶段")
    raw = (config.get("workflows") or {}).get(workflow, {}).get(stage)
    if not isinstance(raw, list) or not raw:
        raise RPAError("当前派单模式的页面动作尚未校准")
    for i, step in enumerate(raw, 1):
        if not isinstance(step, dict) or step.get("action") not in ALLOWED_ACTIONS:
            raise RPAError(f"第 {i} 步动作类型无效")
        selector = step.get("selector")
        if not isinstance(selector, str) or not selector.strip():
            raise RPAError(f"第 {i} 步缺少页面选择器")
        if step["action"] in ("fill", "select", "assert_text"):
            has_field = step.get("field") in ALLOWED_FIELDS
            has_value = isinstance(step.get("value"), str)
            if has_field == has_value:
                raise RPAError(f"第 {i} 步必须且只能指定 field 或 value")
        if step["action"] == "wait_visible":
            timeout = step.get("timeout", 15)
            if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
                raise RPAError(f"第 {i} 步等待时间无效")
    return raw


def readiness(workflow: str, stage: str, config_path: Path = CONFIG_FILE) -> dict:
    try:
        config = load_config(config_path)
        if config.get("enabled") is not True:
            raise RPAError("派单 RPA 尚未启用；需在内网完成页面选择器联调")
        steps = _stage_steps(config, workflow, stage)
        marker = config.get("url_marker")
        if not isinstance(marker, str) or not marker.strip():
            raise RPAError("未配置目标系统 URL 标识")
        port = config.get("debug_port", 9222)
        if not isinstance(port, int) or port < 1024 or port > 65535:
            raise RPAError("浏览器调试端口无效")
        info = browser.check_debug_port(port)
        if not info.ok:
            raise RPAError("本机调试浏览器未就绪；请先启动浏览器并人工登录中台")
        pages = browser.list_pages(port)
        if not any(p.get("type") == "page" and marker in (p.get("url") or "") for p in pages):
            raise RPAError("未找到已登录的目标系统页面")
        return {"ready": True, "message": f"可运行，共 {len(steps)} 步", "steps": len(steps)}
    except RPAError as exc:
        return {"ready": False, "message": str(exc), "steps": 0}


def _element_js(action: str, selector: str, value: str = "") -> str:
    """只生成固定动作的 JS，不执行配置中的任意脚本。"""
    sel, val = json.dumps(selector), json.dumps(value)
    base = (
        "const all=[...document.querySelectorAll(" + sel + ")].filter(e=>"
        "e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');"
        "if(all.length!==1)return {ok:false,message:'匹配元素数 '+all.length};"
        "const el=all[0];"
    )
    if action == "click":
        body = "el.click();return {ok:true};"
    elif action == "fill":
        body = ("if(!('value' in el))return {ok:false,message:'目标不可输入'};"
                "el.focus();el.value=" + val + ";"
                "el.dispatchEvent(new Event('input',{bubbles:true}));"
                "el.dispatchEvent(new Event('change',{bubbles:true}));return {ok:true};")
    elif action == "select":
        body = ("if(el.tagName!=='SELECT')return {ok:false,message:'目标不是下拉框'};"
                "const opt=[...el.options].find(o=>o.value===" + val + "||o.text.trim()===" + val + ");"
                "if(!opt)return {ok:false,message:'没有匹配选项'};"
                "el.value=opt.value;el.dispatchEvent(new Event('change',{bubbles:true}));return {ok:true};")
    elif action == "assert_text":
        body = ("const actual=('value' in el?el.value:el.textContent).trim();"
                "return {ok:actual.includes(" + val + "),message:actual.slice(0,120)};")
    else:
        body = "return {ok:true};"
    return "(()=>{" + base + body + "})()"


def run_steps(cdp: Any, plan: dict, steps: list[dict]) -> list[dict]:
    results: list[dict] = []
    for i, step in enumerate(steps, 1):
        action = step["action"]
        selector = step["selector"]
        value = str(plan.get(step["field"], "")) if "field" in step else step.get("value", "")
        if action == "wait_visible":
            js = _element_js("wait_visible", selector)
            ok = cdp.wait_for(f"(function(){{const r={js};return r.ok}})()",
                              timeout=float(step.get("timeout", 15)))
            result = {"ok": ok, "message": "页面元素出现" if ok else "等待页面元素超时"}
        else:
            result = cdp.eval_js(_element_js(action, selector, value))
        if not isinstance(result, dict) or not result.get("ok"):
            message = result.get("message", "页面动作失败") if isinstance(result, dict) else "页面动作无返回"
            raise RPAError(f"第 {i} 步 {action} 失败：{message}")
        results.append({"step": i, "action": action, "ok": True})
    return results


def execute(plan: dict, stage: str, config_path: Path = CONFIG_FILE) -> dict:
    config = load_config(config_path)
    status = readiness(plan["workflow"], stage, config_path)
    if not status["ready"]:
        raise RPAError(status["message"])
    if stage == "prepare_submit" and plan["status"] != "draft":
        raise RPAError("自动配置仅允许准备中的方案执行")
    if stage == "execute_refresh" and plan["status"] != "approved":
        raise RPAError("必须先在中台完成审批并登记通过状态")
    port = config["debug_port"]
    cdp = browser.connect_page(port, url_contains=config["url_marker"])
    try:
        # 再次核对实际连接页面，避免误操作其他标签页。
        url = cdp.eval_js("location.href")
        if config["url_marker"] not in str(url):
            raise RPAError("浏览器当前页面不是目标系统")
        steps = _stage_steps(config, plan["workflow"], stage)
        return {"ok": True, "steps": run_steps(cdp, plan, steps),
                "message": "浏览器动作已执行；请在中台核对结果后登记状态"}
    finally:
        cdp.close()
