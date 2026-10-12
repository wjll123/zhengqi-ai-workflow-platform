"""任务注册与运行记录

平台的可运维性基础：
- 每个处理能力注册为「任务」
- 每次运行产生一条记录（状态、耗时、输入输出、错误）
- 前端可查看历史与日志
"""
from __future__ import annotations

import json
import time
import traceback
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import STATE_DIR
from .logger import get_logger

log = get_logger("tasks")

RUNS_FILE = STATE_DIR / "runs.json"
MAX_RECORDS = 500


@dataclass
class TaskResult:
    """一次运行的结果"""
    ok: bool
    message: str = ""
    outputs: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class Task:
    """一个处理任务的定义"""
    key: str                       # 唯一标识
    name: str                      # 中文名
    module: str                    # 所属模块
    description: str
    handler: Callable[..., TaskResult]
    params: list[dict] = field(default_factory=list)   # 参数定义
    # 数据源类别：auto（系统可导出·全自动）/ semi（需跑SQL·半自动）/ manual（人工供数）
    source_type: str = "auto"
    source_note: str = ""          # 取数指引（B 类必填）
    outputs_desc: str = ""         # 产出物描述（纯文本，用于指引卡）
    # 需要的源数据声明：[{"label": "人看的名字", "keywords": ["匹配文件名关键词", ...]}]
    # 用于「执行前检查源是否齐备」——缺哪个就明确告诉用户去补哪个
    required_sources: list[dict] = field(default_factory=list)

    def check_sources(self, inbox, batch: str | None = None) -> dict:
        """检查源数据是否齐备（执行前预检）

        ⚠️ 重要：资料包里的样例/参照物**不是**待处理的数据源。
        每次生成新成果，都必须放入**本批次的新数据源**。

        判断分两层：
          1. 关键词及可选扩展名匹配文件名（确认是不是这一类数据）
          2. 若声明了 batch_key，则文件名还需含当前账期（确认是本批次）

        返回：
          {ready, batch, items: [{label, found, file, keywords, batch_ok}]}
        """
        from pathlib import Path as _P
        from .config import get_batch

        if batch is None:
            batch = get_batch()

        inbox = _P(inbox)
        files: list = []
        if inbox.exists():
            files = sorted((
                p for p in inbox.iterdir()
                if p.is_file()
                and not p.name.startswith(("~$", ".~", "."))
                and p.name != ".gitkeep"
            ), key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)

        items: list[dict] = []
        for spec in self.required_sources:
            label = spec.get("label", "")
            kws = spec.get("keywords", []) or []
            need_batch = spec.get("batch_key", False)   # 是否要求匹配账期
            extensions = {ext.lower() for ext in spec.get("extensions", [])}

            hit = next((f for f in files
                        if any(kw in f.name for kw in kws)
                        and (not extensions or f.suffix.lower() in extensions)
                        and (not need_batch or not batch or batch in f.name)), None)

            items.append({
                "label": label,
                "found": hit is not None,
                "file": hit.name if hit else "",
                "keywords": kws,
                "batch_key": need_batch,
            })

        ready = all(i["found"] for i in items) if items else True
        return {"ready": ready, "batch": batch, "items": items}

    @property
    def outputs_desc_html(self) -> str:
        """产出物描述 → HTML（把文件名与 sheet 名高亮加粗）

        识别规则：
          · 形如 `xxx.xlsx` / `xxx.txt` 的**文件名** → <b>
          · 形如 `sheet「xxx」` 的 **sheet 名** → 高亮
        数据源均为代码内常量，安全可控。
        """
        import html as _h
        import re as _re

        text = self.outputs_desc or ""
        if not text:
            return ""

        esc = _h.escape(text)

        # sheet「xxx」 → 高亮
        esc = _re.sub(
            r"sheet「([^」]+)」",
            r'<span style="color:var(--ink);font-weight:600;">sheet「\1」</span>',
            esc,
        )
        # 文件名（含扩展名）→ 加粗等宽
        esc = _re.sub(
            r"([\w\u4e00-\u9fa5\-\.]+\.(?:xlsx|xls|csv|txt|pptx|docx))",
            r"<b>\1</b>",
            esc,
        )
        return esc

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "module": self.module,
            "description": self.description,
            "params": self.params,
            "source_type": self.source_type,
            "source_label": SOURCE_LABELS.get(self.source_type, "未知"),
            "source_note": self.source_note,
            "outputs_desc": self.outputs_desc,
            "outputs_desc_html": self.outputs_desc_html,
            "required_sources": self.required_sources,
        }


# 数据源类别 → 中文标签
# ⚠️ 2026-10-09 起：不做 RPA，统一为「人工导出数据源 → 平台生成成品」
SOURCE_LABELS = {
    "auto": "系统导出 · 直接加工",
    "semi": "需跑SQL · 结果上传",
    "manual": "人工填报 · 模板上传",
}


class Registry:
    """任务注册表"""

    def __init__(self) -> None:
        self._tasks: dict[str, Task] = {}

    def register(
        self,
        key: str,
        name: str,
        module: str,
        description: str = "",
        params: list[dict] | None = None,
        source_type: str = "auto",
        source_note: str = "",
        outputs_desc: str = "",
        required_sources: list[dict] | None = None,
    ):
        def deco(fn: Callable[..., TaskResult]) -> Callable[..., TaskResult]:
            self._tasks[key] = Task(
                key=key, name=name, module=module,
                description=description, handler=fn,
                params=params or [],
                source_type=source_type,
                source_note=source_note,
                outputs_desc=outputs_desc,
                required_sources=required_sources or [],
            )
            return fn
        return deco

    def get(self, key: str) -> Task | None:
        return self._tasks.get(key)

    def all(self) -> list[Task]:
        return list(self._tasks.values())

    def by_module(self) -> dict[str, list[Task]]:
        out: dict[str, list[Task]] = {}
        for t in self._tasks.values():
            out.setdefault(t.module, []).append(t)
        return out


registry = Registry()


def _load_runs() -> list[dict]:
    if not RUNS_FILE.exists():
        return []
    try:
        return json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except Exception:
        log.warning("运行记录文件损坏，已重置")
        return []


def _save_runs(records: list[dict]) -> None:
    records = records[-MAX_RECORDS:]
    RUNS_FILE.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def run_task(key: str, params: dict | None = None, triggered_by: str = "manual") -> dict:
    """执行任务并记录

    执行前会做**源数据预检**：声明的 required_sources 若缺失，
    直接返回缺失清单（不进入加工），避免生成错表。
    """
    task = registry.get(key)
    if task is None:
        return {"ok": False, "message": f"任务不存在: {key}"}

    run_id = uuid.uuid4().hex[:12]
    started = time.time()
    params = params or {}

    log.info("[%s] 开始执行: %s | 参数=%s", run_id, task.name, params or "无")

    # ---------------- 执行前源数据预检 ----------------
    missing: list[dict] = []
    if task.required_sources:
        from .config import INBOX_DIR
        pre = task.check_sources(INBOX_DIR)
        missing = [i for i in pre["items"] if not i["found"]]
        if missing:
            names = "、".join(i["label"] for i in missing)
            result = TaskResult(
                ok=False,
                message=(
                    f"缺少 {len(missing)} 项源数据：{names}\n"
                    f"请先提供这些数据源（上传到收件目录或从界面「上传数据源」），再执行本任务。"
                ),
                metrics={
                    "缺失源": [i["label"] for i in missing],
                    "已就绪源": [i["label"] for i in pre["items"] if i["found"]],
                },
            )
            log.info("[%s] 源预检未通过: 缺 %s", run_id, names)
            elapsed = round(time.time() - started, 2)
            record = {
                "run_id": run_id,
                "task_key": key,
                "task_name": task.name,
                "module": task.module,
                "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "elapsed": elapsed,
                "triggered_by": triggered_by,
                "params": params,
                **asdict(result),
            }
            records = _load_runs()
            records.append(record)
            _save_runs(records)
            log.info("[%s] FAIL | %s | 耗时 %ss | %s", run_id, task.name, elapsed, result.message)
            return record

    try:
        result = task.handler(**params)
    except TypeError as e:
        result = TaskResult(ok=False, message=f"参数错误: {e}")
    except Exception as e:
        log.error("[%s] 执行异常: %s", run_id, e)
        log.error(traceback.format_exc())
        result = TaskResult(
            ok=False,
            message=f"执行异常: {type(e).__name__}: {e}",
            metrics={"traceback": traceback.format_exc()[-1500:]},
        )

    elapsed = round(time.time() - started, 2)
    record = {
        "run_id": run_id,
        "task_key": key,
        "task_name": task.name,
        "module": task.module,
        "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed": elapsed,
        "triggered_by": triggered_by,
        "params": params,
        **asdict(result),
    }

    records = _load_runs()
    records.append(record)
    _save_runs(records)

    level = "OK" if result.ok else "FAIL"
    log.info("[%s] %s | %s | 耗时 %ss | %s", run_id, level, task.name, elapsed, result.message)

    return record


def recent_runs(limit: int = 50) -> list[dict]:
    records = _load_runs()
    return list(reversed(records[-limit:]))


def runs_for(key: str, limit: int = 20) -> list[dict]:
    return [r for r in recent_runs(limit * 3) if r.get("task_key") == key][:limit]
