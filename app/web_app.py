"""政企存量平台 · Flask 应用入口"""
from __future__ import annotations

import hmac
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request
from werkzeug.exceptions import RequestEntityTooLarge

from .core.config import (
    HOST, PORT, DEBUG, INBOX_DIR, OUTPUT_DIR, TEMPLATE_DIR, REFERENCE_DIR, LOG_FILE, TRASH_DIR,
    AUTH_ENABLED, AUTH_USER, AUTH_PASS, AUTH_WHITELIST,
)
from .core.file_trash import FileTrashError, move_many, restore_many
from .core.task_catalog import view_for
from .core.logger import get_logger, setup_logging
from .core.runner import recent_runs, registry, run_task, runs_for

setup_logging()
log = get_logger("web")

# ============================================================
# 任务可见性白名单（2026-10-14）：当前只开放已测试跑通的数据分析任务，
# 其他任务先隐藏，避免用户误点未测试的功能。
# ============================================================
VISIBLE_TASK_KEYS: frozenset[str] = frozenset({"monthly_luozhe", "monthly_4plus2", "qcc_company"})

def _visible_files(d: Path) -> list[str]:
    """列出目录下可见的业务文件（排除临时文件与 .gitkeep）"""
    out: list[str] = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        n = p.name
        if n.startswith(("~$", ".~", ".")) or n == ".gitkeep":
            continue
        out.append(n)
    return sorted(out)



def create_app() -> Flask:
    app = Flask(__name__, template_folder="web/templates", static_folder="web/static")
    app.config["JSON_AS_ASCII"] = False

    @app.errorhandler(RequestEntityTooLarge)
    def _upload_too_large(exc):
        if request.path == "/api/upload":
            return jsonify(ok=False, error="上传文件过大，请压缩文件或拆分后重试。"), 413
        return exc

    # ------------------------------------------------------------ 访问鉴权
    # HTTP Basic 全站鉴权：仅当配置了 ZQC_AUTH_USER + ZQC_AUTH_PASS 时启用。
    # 未配置 -> 完全放行（本地开发不受影响）。放行路径见 AUTH_WHITELIST（探针）。
    @app.before_request
    def _require_auth():
        if not AUTH_ENABLED:
            return None
        if request.path in AUTH_WHITELIST:
            return None
        auth = request.authorization
        ok = bool(auth) and auth.type == "basic" and \
            hmac.compare_digest(auth.username or "", AUTH_USER) and \
            hmac.compare_digest(auth.password or "", AUTH_PASS)
        if ok:
            return None
        # 注意：WWW-Authenticate 是 HTTP 头，必须为 latin-1 可编码字符。
        # realm 用 ASCII（"ZQC Platform"），中文说明放在 body 里，避免 UnicodeEncodeError。
        return Response(
            "需要登录后才能访问".encode("utf-8"), 401,
            {"WWW-Authenticate": 'Basic realm="ZQC Platform", charset="UTF-8"',
             "Content-Type": "text/plain; charset=utf-8"},
        )

    # 加载所有模块（注册任务）
    from .modules import load_all
    load_all()

    # 数据预览蓝图
    from .preview import bp as preview_bp
    app.register_blueprint(preview_bp)
    from .qcc.routes import bp as qcc_bp
    app.register_blueprint(qcc_bp)

    # ------------------------------------------------------------ 页面
    @app.get("/")
    def index():
        modules = {
            module: [view_for(t) for t in tasks
                     if t.key != "make_templates" and t.key in VISIBLE_TASK_KEYS]
            for module, tasks in registry.by_module().items()
        }
        # 过滤后可能出现空模块组，顺手去掉空组更清爽
        modules = {k: v for k, v in modules.items() if v}
        return render_template(
            "index.html",
            modules=modules,
            task_count=sum(len(tasks) for tasks in modules.values()),
        )

    @app.get("/task/<key>")
    def task_page(key: str):
        task = registry.get(key)
        if not task:
            return "任务不存在", 404
        return render_template("task.html", task=task, view=view_for(task), histories=runs_for(key, 10))

    @app.get("/runs")
    def runs_page():
        return render_template("runs.html", runs=recent_runs(100))

    @app.get("/dispatch")
    def dispatch_page():
        return render_template("dispatch.html")

    @app.get("/api/dispatch/config")
    def api_dispatch_config():
        from .core.dispatch import WORKFLOWS
        return jsonify({"workflows": WORKFLOWS, "mode": "离线辅助；外部系统操作需人工完成"})

    @app.get("/api/dispatch/plans")
    def api_dispatch_plans():
        from .core.dispatch import list_plans
        return jsonify(list_plans())

    @app.post("/api/dispatch/plans")
    def api_dispatch_create():
        from .core.dispatch import DispatchError, create_plan
        try:
            return jsonify(create_plan(request.get_json(silent=True) or {})), 201
        except DispatchError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/dispatch/plans/<plan_id>")
    def api_dispatch_plan(plan_id: str):
        from .core.dispatch import get_plan
        plan = get_plan(plan_id)
        return jsonify(plan) if plan else (jsonify({"error": "派单方案不存在"}), 404)

    @app.post("/api/dispatch/plans/<plan_id>/checks")
    def api_dispatch_check(plan_id: str):
        from .core.dispatch import DispatchError, set_check
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(set_check(plan_id, str(data.get("code", "")),
                                      data.get("checked") is True, data.get("evidence", "")))
        except DispatchError as e:
            return jsonify({"error": str(e)}), 400

    @app.post("/api/dispatch/plans/<plan_id>/advance")
    def api_dispatch_advance(plan_id: str):
        from .core.dispatch import DispatchError, advance_plan
        data = request.get_json(silent=True) or {}
        try:
            return jsonify(advance_plan(plan_id, str(data.get("target", "")), data))
        except DispatchError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/dispatch/plans/<plan_id>/card")
    def api_dispatch_card(plan_id: str):
        from .core.dispatch import action_card, get_plan
        plan = get_plan(plan_id)
        if not plan:
            return jsonify({"error": "派单方案不存在"}), 404
        return Response(action_card(plan), mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=dispatch-{plan_id[:8]}.txt"})

    @app.get("/api/dispatch/plans/<plan_id>/rpa-ready")
    def api_dispatch_rpa_ready(plan_id: str):
        from .core.dispatch import get_plan
        from .core.dispatch_rpa import readiness
        plan = get_plan(plan_id)
        if not plan:
            return jsonify({"error": "派单方案不存在"}), 404
        stage = "execute_refresh" if plan["status"] == "approved" else "prepare_submit"
        result = readiness(plan["workflow"], stage)
        result["stage"] = stage
        result["already_attempted"] = any(e["action"] == f"rpa_start_{stage}" for e in plan["events"])
        if plan["status"] not in ("draft", "approved"):
            result.update(ready=False, message="当前方案状态不支持自动执行")
        if result["already_attempted"]:
            result.update(ready=False, message="该阶段已尝试自动执行，请先核对中台状态")
        return jsonify(result)

    @app.post("/api/dispatch/plans/<plan_id>/rpa-run")
    def api_dispatch_rpa_run(plan_id: str):
        from .core.dispatch import DispatchError, begin_rpa, finish_rpa, get_plan
        from .core.dispatch_rpa import RPAError, execute, readiness
        plan = get_plan(plan_id)
        if not plan:
            return jsonify({"error": "派单方案不存在"}), 404
        data = request.get_json(silent=True) or {}
        stage = data.get("stage")
        if stage not in ("prepare_submit", "execute_refresh"):
            return jsonify({"error": "未知自动执行阶段"}), 400
        if (stage == "prepare_submit" and plan["status"] != "draft") or (
            stage == "execute_refresh" and plan["status"] != "approved"
        ):
            return jsonify({"error": "当前状态不允许执行该阶段"}), 400
        ready = readiness(plan["workflow"], stage)
        if not ready["ready"]:
            return jsonify({"error": ready["message"]}), 400
        try:
            begin_rpa(plan_id, stage)
        except DispatchError as e:
            return jsonify({"error": str(e)}), 409
        try:
            result = execute(plan, stage)
            finish_rpa(plan_id, stage, True, result["message"])
            return jsonify(result)
        except (RPAError, OSError, RuntimeError) as e:
            finish_rpa(plan_id, stage, False, str(e))
            return jsonify({"error": str(e), "partial_execution_possible": True}), 500

    @app.get("/health")
    def health():
        """平台自检（深度健康检查）"""
        from .core.health import run_health_check
        return jsonify(run_health_check().to_dict())

    @app.get("/healthz")
    def healthz():
        """轻量存活探针（给监控/守护脚本用，只回 ok）"""
        return jsonify({"ok": True})

    @app.get("/health/page")
    def health_page():
        return render_template("health.html")

    # ------------------------------------------------------------ API
    @app.get("/api/tasks")
    def api_tasks():
        return jsonify([t.to_dict() for t in registry.all() if t.key in VISIBLE_TASK_KEYS])

    @app.get("/api/sources/<key>")
    def api_sources(key: str):
        """某任务所需的源数据就绪状态"""
        task = registry.get(key)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        pre = task.check_sources(INBOX_DIR)
        return jsonify({
            "ok": True,
            "task": task.name,
            "ready": pre["ready"],
            "items": pre["items"],
        })

    @app.get("/api/sources")
    def api_sources_all():
        """全部任务的源数据就绪总览（仅显示白名单任务）"""
        out = []
        for t in registry.all():
            if t.key not in VISIBLE_TASK_KEYS:
                continue
            if not t.required_sources:
                continue
            pre = t.check_sources(INBOX_DIR)
            out.append({
                "key": t.key,
                "task": t.name,
                "ready": pre["ready"],
                "missing": [i["label"] for i in pre["items"] if not i["found"]],
                "total": len(pre["items"]),
            })
        return jsonify(out)

    @app.get("/api/batch")
    def api_batch_get():
        from .core.config import get_batch
        return jsonify({"ok": True, "batch": get_batch()})

    @app.post("/api/batch")
    def api_batch_set():
        from .core.config import set_batch
        data = request.get_json(silent=True) or {}
        v = (data.get("batch") or "").strip()
        if not v:
            return jsonify({"ok": False, "error": "账期不能为空"}), 400
        return jsonify({"ok": True, "batch": set_batch(v)})

    @app.get("/api/guide/<key>")
    def api_guide(key: str):
        """取数指引卡（半自动 / 人工供数 类任务）"""
        task = registry.get(key)
        if not task:
            return jsonify({"ok": False, "error": "任务不存在"}), 404
        return jsonify({
            "ok": True,
            "key": task.key,
            "name": task.name,
            "source_type": task.source_type,
            "source_label": task.to_dict()["source_label"],
            "guide": task.source_note or "该任务为全自动类型：源数据由系统导出后，直接放入收件目录即可。",
            "outputs_desc": task.outputs_desc,
        })

    @app.post("/api/run/<key>")
    def api_run(key: str):
        params = request.get_json(silent=True) or {}
        result = run_task(key, params, triggered_by="web")
        return jsonify(result)

    @app.get("/api/runs")
    def api_runs():
        limit = int(request.args.get("limit", 50))
        return jsonify(recent_runs(limit))

    @app.get("/api/inbox")
    def api_inbox():
        return jsonify({"dir": str(INBOX_DIR), "files": _visible_files(INBOX_DIR)})

    # -------------------------------------------------- 数据源上传 / 下载 / 删除
    ALLOWED_EXT = {".xlsx", ".xls", ".xlsm", ".csv", ".txt", ".json", ".pptx", ".docx"}

    @app.post("/api/upload")
    def api_upload():
        """上传数据源到 inbox（供 B 类人工取数、C 类人工供数使用）"""
        import re
        from werkzeug.utils import secure_filename

        files = request.files.getlist("files")
        if not files:
            return jsonify({"ok": False, "error": "没有收到文件"}), 400

        saved: list[str] = []
        skipped: list[str] = []

        for f in files:
            if not f or not f.filename:
                continue
            raw = f.filename
            suffix = Path(raw).suffix.lower()

            if suffix not in ALLOWED_EXT:
                skipped.append(f"{raw}（不支持的格式 {suffix}）")
                continue

            # 保留中文名：只做最小清洗（去路径分隔符与危险字符）
            name = Path(raw).name
            name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip()
            if not name or name in (".", ".."):
                skipped.append(f"{raw}（文件名非法）")
                continue

            target = INBOX_DIR / name
            # 重名时加序号，不覆盖
            if target.exists():
                stem, ext = target.stem, target.suffix
                for i in range(1, 100):
                    cand = INBOX_DIR / f"{stem}({i}){ext}"
                    if not cand.exists():
                        target = cand
                        break

            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                f.save(str(target))
            except OSError as exc:
                log.exception("保存上传文件失败: %s", target)
                skipped.append(f"{raw}（保存失败：{exc.strerror or '文件系统错误'}）")
                continue
            saved.append(target.name)
            log.info("上传数据源: %s", target.name)

        ok = len(saved) > 0
        return jsonify({
            "ok": ok,
            "saved": saved,
            "skipped": skipped,
            "message": f"已上传 {len(saved)} 个文件" + (f"，跳过 {len(skipped)} 个" if skipped else ""),
        })

    def _safe_any(name: str, *dirs: Path):
        """在指定目录里安全定位文件（防目录穿越）"""
        for d in dirs:
            p = (d / name).resolve()
            try:
                p.relative_to(d.resolve())
            except ValueError:
                continue
            if p.exists() and p.is_file():
                return p
        return None

    @app.get("/api/download/<path:name>")
    def api_download(name: str):
        """下载文件（数据源或成品）"""
        from flask import send_file

        p = _safe_any(name, INBOX_DIR, OUTPUT_DIR, TEMPLATE_DIR, REFERENCE_DIR)
        if p is None:
            return jsonify({"ok": False, "error": "文件不存在"}), 404
        return send_file(p, as_attachment=True, download_name=p.name)

    @app.post("/api/delete")
    def api_delete():
        """Move one file to the reversible trash."""
        body = request.get_json(silent=True) or {}
        item = {"where": body.get("where", "inbox"), "name": body.get("name")}
        try:
            result = move_many([item], {"inbox": INBOX_DIR, "outputs": OUTPUT_DIR}, TRASH_DIR)
        except FileTrashError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        log.info("移入回收区: %s/%s", item["where"], item["name"])
        return jsonify({"ok": True, "message": "文件已移入回收区，可撤销",
                        "undo_token": result["token"], "deleted": result["items"]})

    @app.post("/api/delete-batch")
    def api_delete_batch():
        body = request.get_json(silent=True) or {}
        try:
            result = move_many(body.get("items"), {"inbox": INBOX_DIR, "outputs": OUTPUT_DIR}, TRASH_DIR)
        except FileTrashError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        log.info("批量移入回收区: %d 个文件", len(result["items"]))
        return jsonify({"ok": True, "message": f"已移入回收区 {len(result['items'])} 个文件，可撤销",
                        "undo_token": result["token"], "deleted": result["items"]})

    @app.post("/api/restore-batch")
    def api_restore_batch():
        body = request.get_json(silent=True) or {}
        try:
            restored = restore_many(body.get("undo_token"), {"inbox": INBOX_DIR,
                "outputs": OUTPUT_DIR}, TRASH_DIR)
        except (FileTrashError, OSError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400
        log.info("从回收区恢复: %d 个文件", len(restored))
        return jsonify({"ok": True, "message": f"已恢复 {len(restored)} 个文件", "restored": restored})

    @app.get("/api/templates")
    def api_templates():
        """列出可用模板（供 C 类人工供数下载）"""
        files = []
        for n in _visible_files(TEMPLATE_DIR):
            p = TEMPLATE_DIR / n
            files.append({"name": n, "size": p.stat().st_size})
        return jsonify({"dir": str(TEMPLATE_DIR), "files": files, "names": _visible_files(TEMPLATE_DIR)})

    @app.get("/api/outputs")
    def api_outputs():
        files = []
        for n in _visible_files(OUTPUT_DIR):
            p = OUTPUT_DIR / n
            files.append({"name": n, "size": p.stat().st_size, "mtime": int(p.stat().st_mtime)})
        return jsonify({"dir": str(OUTPUT_DIR), "files": files, "names": _visible_files(OUTPUT_DIR)})

    @app.get("/api/log")
    def api_log():
        lines = int(request.args.get("lines", 200))
        if not LOG_FILE.exists():
            return jsonify({"lines": []})
        content = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        return jsonify({"lines": content[-lines:]})

    return app


app = create_app()


if __name__ == "__main__":
    log.info("=" * 60)
    log.info("政企存量平台启动")
    log.info("源数据目录 : %s", INBOX_DIR)
    log.info("成品目录   : %s", OUTPUT_DIR)
    log.info("已注册任务 : %d 个", len(registry.all()))
    log.info("访问地址   : http://%s:%s", HOST, PORT)
    log.info("=" * 60)
    app.run(host=HOST, port=PORT, debug=DEBUG, use_reloader=False)
