import uuid
from flask import Blueprint, jsonify, request, send_file
from .errors import QueryBusy, QueryError
from .jobs import jobs
from .sources import read_names
from ..core.config import WORKER_TOKEN

bp = Blueprint("qcc", __name__, url_prefix="/api/qcc")


def _worker_auth():
    if not WORKER_TOKEN:
        return jsonify(ok=False, message="云端尚未配置桌面助手密钥，请先设置 ZQC_WORKER_TOKEN。"), 503
    if WORKER_TOKEN and request.headers.get("X-ZQC-Worker-Token") != WORKER_TOKEN:
        return jsonify(ok=False, message="桌面助手认证失败。"), 401
    return None


@bp.errorhandler(QueryError)
def error(exc):
    return jsonify(ok=False, message=str(exc)), 409 if isinstance(exc, QueryBusy) else 400


@bp.get("/company-count")
def count():
    names = read_names(request.args.get("source_name", ""), jobs.inbox)
    return jsonify(ok=True, count=len(names), names=names)


@bp.get("/jobs")
def recent():
    return jsonify(ok=True, jobs=jobs.recent())


@bp.post("/jobs")
def start():
    return jsonify(ok=True, job=jobs.start(request.get_json(silent=True))), 202


@bp.get("/jobs/<job_id>")
def status(job_id):
    return jsonify(ok=True, job=jobs.get(job_id))


@bp.post("/jobs/<job_id>/pause")
def pause(job_id):
    return jsonify(ok=True, job=jobs.pause(job_id))


@bp.post("/jobs/<job_id>/resume")
def resume(job_id):
    return jsonify(ok=True, job=jobs.resume(job_id)), 202


@bp.get("/jobs/<job_id>/export")
def export(job_id):
    return send_file(jobs.export(job_id), as_attachment=True)


@bp.get("/previous-results")
def previous_results():
    files = sorted(jobs.output.glob("企查查-*批量查询-*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify(ok=True, files=[p.name for p in files])


@bp.get("/browser-status")
def browser_status():
    from .chromium import browser_status as probe
    result = []
    for name, port in (("chrome", 9222), ("edge", 9223)):
        status = probe(name, port)
        status.pop("pages", None)
        status.pop("all_pages", None)
        result.append(status)
    return jsonify(ok=True, browsers=result)


@bp.post("/worker/claim")
def worker_claim():
    denied = _worker_auth()
    if denied:
        return denied
    worker_id = (request.get_json(silent=True) or {}).get("worker_id", "")
    return jsonify(ok=True, job=jobs.claim_worker(worker_id))


@bp.post("/worker/jobs/<job_id>/update")
def worker_update(job_id):
    denied = _worker_auth()
    if denied:
        return denied
    body = request.get_json(silent=True) or {}
    worker_id = body.pop("worker_id", "")
    return jsonify(ok=True, **jobs.worker_update(job_id, worker_id, body))


@bp.post("/worker/jobs/<job_id>/result")
def worker_result(job_id):
    denied = _worker_auth()
    if denied:
        return denied
    worker_id = request.form.get("worker_id", "")
    uploaded = request.files.get("file")
    if not uploaded or not uploaded.filename:
        raise QueryError("桌面助手没有上传结果文件。")
    temp = jobs.root / ("worker-" + uuid.uuid4().hex + ".xlsx")
    temp.parent.mkdir(parents=True, exist_ok=True)
    uploaded.save(temp)
    try:
        return jsonify(ok=True, job=jobs.attach_worker_result(job_id, worker_id, temp))
    finally:
        temp.unlink(missing_ok=True)
