"""政企存量平台 · 命令行入口（无需网页服务）

用法：
  python -m app.cli list              列出所有任务
  python -m app.cli run <key>         执行指定任务
  python -m app.cli run-all           执行全部任务
  python -m app.cli runs              查看运行记录
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core.config import INBOX_DIR, OUTPUT_DIR
from .core.logger import get_logger
from .core.runner import recent_runs, registry, run_task
from .modules import load_all

log = get_logger("cli")

# 按执行顺序排列（组合任务单独处理）
ORDER = [
    "weekly_all",
    "whitepaper_211",
    "material_notice_text",
]


def cmd_list(_args) -> int:
    load_all()
    by_mod: dict[str, list] = {}
    for t in registry.all():
        by_mod.setdefault(t.module, []).append(t)

    print()
    print("=" * 70)
    print(f"  已注册任务（{len(registry.all())} 个）")
    print("=" * 70)
    for mod, tasks in by_mod.items():
        print(f"\n【{mod}】")
        for t in tasks:
            print(f"  {t.key:26s}  {t.name}")
    print()
    return 0


def cmd_run(args) -> int:
    load_all()
    params = {}
    if args.month:
        params["month"] = args.month

    rec = run_task(args.key, params)
    return _report(rec)


def cmd_run_all(_args) -> int:
    load_all()

    keys = [k for k in ORDER if registry.get(k)]
    # 补上未列在 ORDER 里的单表任务
    for t in registry.all():
        if t.key not in keys and t.key != "weekly_all":
            keys.append(t.key)

    total = len(keys)
    ok = 0
    outputs: list[str] = []

    for i, key in enumerate(keys, 1):
        task = registry.get(key)
        print(f"[{i}/{total}] {task.name} ...", end=" ", flush=True)
        rec = run_task(key, triggered_by="cli")
        if rec["ok"]:
            ok += 1
            outputs.extend(rec.get("outputs", []))
            print(f"✓ {rec['elapsed']}s")
        else:
            print(f"✗ {rec['message']}")

    print()
    print("=" * 70)
    print(f"  完成 {ok}/{total}，产出 {len(dict.fromkeys(outputs))} 个文件")
    print("=" * 70)
    for o in dict.fromkeys(outputs):
        print(f"  ✓ {Path(o).name}")
    print()
    return 0 if ok == total else 1


def cmd_runs(args) -> int:
    recs = recent_runs(args.limit)
    print()
    print(f"最近 {len(recs)} 条运行记录：")
    print("-" * 70)
    for r in recs:
        mark = "✓" if r["ok"] else "✗"
        print(f"  {r['started_at']}  {mark}  {r['task_name']:28s} {r['elapsed']:>6}s  {r['message'][:40]}")
    print()
    return 0


def _report(rec: dict) -> int:
    print()
    print("=" * 70)
    print(f"  {'✓ 成功' if rec['ok'] else '✗ 失败'}  |  {rec['task_name']}")
    print("=" * 70)
    print(f"  {rec['message']}")
    print(f"  耗时 {rec['elapsed']}s  |  run_id {rec['run_id']}")
    if rec.get("outputs"):
        print("\n  产出文件：")
        for o in rec["outputs"]:
            print(f"    · {o}")
    if rec.get("warnings"):
        print("\n  告警：")
        for w in rec["warnings"]:
            print(f"    ⚠️ {w}")
    print()
    return 0 if rec["ok"] else 1


def cmd_health(_args) -> int:
    """平台自检"""
    from .core.health import run_health_check
    from .modules import load_all

    load_all()          # 确保任务已注册（Web 端由 create_app 加载）

    d = run_health_check().to_dict()
    ICON = {"ok": "✓", "warn": "⚠", "error": "✗", "info": "ℹ"}
    COLORS = {"ok": "\033[32m", "warn": "\033[33m", "error": "\033[31m", "info": "\033[90m"}
    RESET = "\033[0m"

    print()
    print("=" * 72)
    print("  平台健康检查")
    print("=" * 72)
    print()

    for c in d["checks"]:
        lv = c["level"]
        icon = ICON.get(lv, "·")
        color = COLORS.get(lv, "")
        print(f"  {color}{icon}{RESET} {c['name']:8s}  {c['detail']}")
        if c["advice"]:
            print(f"      {COLORS['warn']}→ {c['advice']}{RESET}")

    print()
    print("-" * 72)
    lv = d["level"]
    color = COLORS.get(lv, "")
    print(f"  {color}{d['summary']}{RESET}")
    print(f"  检查时间：{d['checked_at']}")
    print("-" * 72)
    print()
    return 0 if d["ok"] else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="app.cli", description="政企存量平台 CLI")
    sub = ap.add_subparsers(dest="cmd")

    sub.add_parser("list", help="列出所有任务").set_defaults(func=cmd_list)

    p_run = sub.add_parser("run", help="执行指定任务")
    p_run.add_argument("key", help="任务 key")
    p_run.add_argument("--month", help="月份（通报文案用）")
    p_run.set_defaults(func=cmd_run)

    sub.add_parser("run-all", help="执行全部任务").set_defaults(func=cmd_run_all)

    p_runs = sub.add_parser("runs", help="查看运行记录")
    p_runs.add_argument("--limit", type=int, default=20)
    p_runs.set_defaults(func=cmd_runs)

    sub.add_parser("health", help="平台自检（环境/依赖/目录/磁盘/任务/日志）").set_defaults(func=cmd_health)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        print(f"源数据目录: {INBOX_DIR}")
        print(f"成品目录  : {OUTPUT_DIR}")
        ap.print_help()
        return 0

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
