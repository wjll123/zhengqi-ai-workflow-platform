from flask import Blueprint, jsonify, request, send_file
from .errors import QueryBusy, QueryError
from .jobs import jobs
from .sources import read_names

bp = Blueprint("qcc", __name__, url_prefix="/api/qcc")


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
