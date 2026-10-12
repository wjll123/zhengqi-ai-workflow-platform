"""平台健康检查（自检）

从「回一个 ok」升级为**真正的自检**：把平台跑起来所需的每一项都验一遍，
并给出**人话结论 + 可执行建议**。

检查维度：
  1. 运行环境   —— Python 版本、依赖包是否齐备
  2. 目录可用   —— inbox / outputs / logs / templates / config 是否存在且可写
  3. 磁盘空间   —— 剩余空间是否够（低于阈值报警）
  4. 任务加载   —— 注册的任务数、是否有异常
  5. 运行记录   —— 最近一次执行成功/失败、连续失败次数
  6. 源数据     —— 收件目录里有多少文件（提示可以跑什么）
  7. 日志       —— 日志文件是否正常滚动、最近有无报错
"""
from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


# ---------------------------------------------------------------- 数据结构

@dataclass
class Check:
    """单项检查结果"""
    name: str                     # 检查项名
    ok: bool                      # 是否通过
    level: str = "info"           # ok / warn / error / info
    detail: str = ""              # 详细说明
    advice: str = ""              # 有问题时的建议动作

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "level": self.level,
            "detail": self.detail,
            "advice": self.advice,
        }


@dataclass
class HealthReport:
    ok: bool = True
    level: str = "ok"             # 总体：ok / warn / error
    summary: str = ""
    checks: list[Check] = field(default_factory=list)
    facts: dict = field(default_factory=dict)
    checked_at: str = ""

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "level": self.level,
            "summary": self.summary,
            "checks": [c.to_dict() for c in self.checks],
            "facts": self.facts,
            "checked_at": self.checked_at,
        }


# ---------------------------------------------------------------- 检查项

REQUIRED_PKGS = [
    ("flask", "Flask"),
    ("openpyxl", "openpyxl"),
    ("xlrd", "xlrd"),
    ("pptx", "python-pptx"),
]

MIN_FREE_MB = 500          # 磁盘剩余告警阈值（MB）


def check_python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    ok = (v.major, v.minor) >= (3, 10)
    return Check(
        name="运行环境",
        ok=ok,
        level="ok" if ok else "error",
        detail=f"Python {ver}",
        advice="" if ok else "需要 Python 3.10 及以上版本",
    )


def check_deps() -> Check:
    missing: list[str] = []
    found: list[str] = []
    for mod, label in REQUIRED_PKGS:
        try:
            __import__(mod)
            found.append(label)
        except Exception:
            missing.append(label)

    ok = not missing
    return Check(
        name="依赖包",
        ok=ok,
        level="ok" if ok else "error",
        detail=(
            f"已安装 {len(found)}/{len(REQUIRED_PKGS)}：" + "、".join(found)
            if ok else
            f"缺少：{'、'.join(missing)}"
        ),
        advice="" if ok else "执行：./.venv/bin/pip install -r requirements.txt",
    )


def check_dirs(inbox: Path, outputs: Path, logs: Path,
               templates: Path, config: Path) -> Check:
    dirs = {
        "收件目录": inbox,
        "成品目录": outputs,
        "日志目录": logs,
        "模板目录": templates,
        "配置目录": config,
    }
    problems: list[str] = []
    for label, d in dirs.items():
        try:
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".health_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except Exception as e:
            problems.append(f"{label}({d.name}) 不可写：{e}")

    ok = not problems
    return Check(
        name="目录读写",
        ok=ok,
        level="ok" if ok else "error",
        detail="5 个目录均可读写" if ok else "；".join(problems),
        advice="" if ok else "检查目录权限，或确认磁盘未满",
    )


def check_disk(path: Path) -> Check:
    try:
        usage = shutil.disk_usage(path)
        free_mb = usage.free / 1024 / 1024
        total_gb = usage.total / 1024 / 1024 / 1024
        used_pct = usage.used / usage.total * 100

        if free_mb < MIN_FREE_MB:
            level, ok = "error", False
            advice = "清理磁盘，或把 data/ 迁到大容量分区"
        elif free_mb < MIN_FREE_MB * 4:
            level, ok = "warn", True
            advice = "磁盘剩余偏少，建议清理"
        else:
            level, ok = "ok", True
            advice = ""

        return Check(
            name="磁盘空间",
            ok=ok,
            level=level,
            detail=f"剩余 {free_mb / 1024:.1f} GB / 共 {total_gb:.0f} GB（已用 {used_pct:.0f}%）",
            advice=advice,
        )
    except Exception as e:
        return Check("磁盘空间", False, "warn", f"无法获取：{e}", "请手动确认磁盘空间")


def check_tasks() -> tuple[Check, int]:
    try:
        from .runner import registry
        tasks = registry.all()
        n = len(tasks)
        if n == 0:
            return Check("任务加载", False, "error",
                         "没有加载到任何任务", "检查 app/modules/__init__.py 的 load_all()"), 0
        modules = sorted({t.module for t in tasks})
        return Check(
            "任务加载", True, "ok",
            f"{n} 个任务，分属 {len(modules)} 个模块（{'、'.join(modules)}）",
        ), n
    except Exception as e:
        return Check("任务加载", False, "error", f"加载失败：{e}",
                     "查看日志定位导入错误"), 0


def check_runs() -> Check:
    try:
        from .runner import recent_runs
        runs = recent_runs(50)
        if not runs:
            return Check("运行记录", True, "info", "还没有执行过任务", "")

        last = runs[0]
        last_ok = bool(last.get("ok"))
        last_name = last.get("task_name") or last.get("task_key") or "?"

        # 连续失败次数（从最近往前数）
        streak = 0
        for r in runs:
            if r.get("ok"):
                break
            streak += 1

        if streak >= 3:
            return Check(
                "运行记录", False, "warn",
                f"最近连续 {streak} 次执行失败，最后一次「{last_name}」",
                "打开「运行记录」页查看失败原因（通常缺源数据或格式不符）",
            )
        if not last_ok:
            return Check(
                "运行记录", True, "warn",
                f"最后一次「{last_name}」失败",
                "多为缺少源数据，按界面提示补齐即可",
            )
        return Check("运行记录", True, "ok",
                     f"最近一次「{last_name}」执行成功，共 {len(runs)} 条历史")
    except Exception as e:
        return Check("运行记录", False, "warn", f"读取失败：{e}", "")


def check_inbox(inbox: Path) -> tuple[Check, int]:
    try:
        files = [
            p for p in inbox.iterdir()
            if p.is_file() and not p.name.startswith(("~$", ".~", "."))
            and p.name != ".gitkeep"
        ]
        n = len(files)
        if n == 0:
            return Check(
                "源数据", True, "info",
                "收件目录为空",
                "把源数据放进 data/inbox/，或从界面上传",
            ), 0
        # 只列前几个，避免太长
        names = "、".join(f.name for f in sorted(files, key=lambda x: x.name)[:4])
        more = f" 等 {n} 个" if n > 4 else ""
        return Check("源数据", True, "ok", f"共 {n} 个文件：{names}{more}"), n
    except Exception as e:
        return Check("源数据", False, "warn", f"读取失败：{e}", ""), 0


def check_logs(log_file: Path) -> Check:
    try:
        if not log_file.exists():
            return Check("日志文件", True, "info", "日志尚未生成", "")
        size_mb = log_file.stat().st_size / 1024 / 1024
        # 扫最近 200 行找 ERROR
        lines = log_file.read_text(encoding="utf-8", errors="ignore").splitlines()[-200:]
        errors = [ln for ln in lines if " ERROR " in ln or "Traceback" in ln]
        if errors:
            return Check(
                "日志文件", True, "warn",
                f"{size_mb:.1f} MB，最近 200 行有 {len(errors)} 条错误",
                "查看 data/logs/ 定位；若为单个任务报错，通常补源数据即可",
            )
        return Check("日志文件", True, "ok", f"{size_mb:.1f} MB，近期无异常")
    except Exception as e:
        return Check("日志文件", False, "warn", f"读取失败：{e}", "")


# ---------------------------------------------------------------- 汇总

def run_health_check() -> HealthReport:
    """执行全部检查，返回汇总报告"""
    from .config import INBOX_DIR, OUTPUT_DIR, LOG_DIR, TEMPLATE_DIR, CONFIG_DIR, LOG_FILE

    checks: list[Check] = []

    checks.append(check_python())
    checks.append(check_deps())
    checks.append(check_dirs(INBOX_DIR, OUTPUT_DIR, LOG_DIR, TEMPLATE_DIR, CONFIG_DIR))
    checks.append(check_disk(OUTPUT_DIR))

    task_check, task_n = check_tasks()
    checks.append(task_check)
    checks.append(check_runs())

    inbox_check, inbox_n = check_inbox(INBOX_DIR)
    checks.append(inbox_check)
    checks.append(check_logs(LOG_FILE))

    # ---- 总体判定
    has_error = any(c.level == "error" for c in checks)
    has_warn = any(c.level == "warn" for c in checks)

    if has_error:
        level, ok = "error", False
        bad = [c.name for c in checks if c.level == "error"]
        summary = f"发现 {len(bad)} 项严重问题：{'、'.join(bad)}"
    elif has_warn:
        level, ok = "warn", True
        bad = [c.name for c in checks if c.level == "warn"]
        summary = f"平台可用，但有 {len(bad)} 项需留意：{'、'.join(bad)}"
    else:
        level, ok = "ok", True
        summary = "平台各项正常，可以开始处理任务"

    return HealthReport(
        ok=ok,
        level=level,
        summary=summary,
        checks=checks,
        facts={
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "tasks": task_n,
            "inbox_files": inbox_n,
            "inbox_dir": str(INBOX_DIR),
            "outputs_dir": str(OUTPUT_DIR),
        },
        checked_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
