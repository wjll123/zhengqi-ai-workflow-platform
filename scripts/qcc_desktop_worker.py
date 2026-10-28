#!/usr/bin/env python3
"""Cloud queue worker for the QCC desktop execution mode.

Run this on the Mac that has a logged-in QCC browser. The worker polls the
cloud platform; it never exposes the local CDP port to the internet.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.qcc.jobs import QueryJobs


def auth_headers(token="", user="", password=""):
    headers = {}
    if token:
        headers["X-ZQC-Worker-Token"] = token
    if user or password:
        raw = f"{user}:{password}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    return headers


def request_json(base, path, *, token="", user="", password="", payload=None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode()
    headers = {"Accept": "application/json", **auth_headers(token, user, password)}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["X-ZQC-Worker-Token"] = token
    request = urllib.request.Request(base.rstrip("/") + path, data=body, headers=headers,
                                     method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"云端接口 HTTP {exc.code}: {detail}") from exc


def download(base, name, destination, token="", user="", password=""):
    headers = auth_headers(token, user, password)
    url = base.rstrip("/") + "/api/download/" + urllib.parse.quote(name, safe="")
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=120) as response:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(response.read())


def update(base, job_id, worker_id, token, user="", password="", **payload):
    payload["worker_id"] = worker_id
    return request_json(base, f"/api/qcc/worker/jobs/{job_id}/update", token=token, user=user, password=password, payload=payload)


def upload_result(base, job_id, worker_id, token, user, password, path):
    boundary = "----zqc-worker-" + worker_id.replace("-", "")
    data = path.read_bytes()
    # Build explicitly to keep the multipart body compatible with Python 3.9.
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"worker_id\"\r\n\r\n"
            f"{worker_id}\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{path.name}\"\r\n"
            "Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body)), **auth_headers(token, user, password)}
    request = urllib.request.Request(base.rstrip("/") + f"/api/qcc/worker/jobs/{job_id}/result", data=body, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def sync_local_job(base, cloud_job, local_jobs, worker_id, token):
    local = local_jobs.get(cloud_job["local_id"])
    rows = local.get("rows", [])
    result = update(base, cloud_job["id"], worker_id, token, status=local["status"],
                    message=local.get("message", ""), current_name=local.get("current_name", ""), rows=rows)
    return result


def run_one(base, token, user, password, worker_id, cloud_job, poll_seconds):
    inbox = ROOT / "data" / "inbox"
    source = cloud_job["source_name"]
    download(base, source, inbox / source, token, user, password)
    identity = cloud_job.get("identity_source", "")
    if identity:
        download(base, identity, inbox / identity, token, user, password)
    state = ROOT / "data" / "state" / "qcc-desktop-worker"
    output = ROOT / "data" / "outputs"
    local_jobs = QueryJobs(state, inbox, output)
    params = {"source_name": source, "identity_source": identity,
              "max_new_queries": cloud_job["max_new_queries"],
              "delay_seconds": cloud_job["delay_seconds"],
              "browser": cloud_job.get("browser", "chrome"),
              "debug_port": cloud_job.get("debug_port", 9222), "execution": "local"}
    local = local_jobs.start(params)
    cloud_job["local_id"] = local["id"]
    while True:
        current = local_jobs.get(local["id"])
        result = update(base, cloud_job["id"], worker_id, token, user, password,
                        status=current["status"], message=current.get("message", ""),
                        current_name=current.get("current_name", ""), rows=current.get("rows", []))
        if result.get("stop") and current["status"] == "running":
            local_jobs.pause(local["id"])
        if current["status"] in {"blocked", "paused", "completed"}:
            if current["status"] == "completed":
                result_path = local_jobs.export(local["id"])
                upload_result(base, cloud_job["id"], worker_id, token, user, password, result_path)
            return
        time.sleep(poll_seconds)


def main():
    parser = argparse.ArgumentParser(description="企查查云端派单桌面助手")
    parser.add_argument("--server", required=True, help="云端平台地址，例如 http://127.0.0.1:8849")
    parser.add_argument("--token", default=os.environ.get("ZQC_WORKER_TOKEN", ""))
    parser.add_argument("--user", default=os.environ.get("ZQC_AUTH_USER", ""))
    parser.add_argument("--password", default=os.environ.get("ZQC_AUTH_PASS", ""))
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--poll", type=int, default=5, help="轮询间隔秒数")
    args = parser.parse_args()
    print(f"桌面助手已启动：{args.worker_id}，等待云端任务。", flush=True)
    while True:
        try:
            response = request_json(args.server, "/api/qcc/worker/claim", token=args.token, user=args.user, password=args.password,
                                    payload={"worker_id": args.worker_id})
            job = response.get("job")
            if job:
                print(f"领取任务 {job['id']}：{job['source_name']}", flush=True)
                try:
                    run_one(args.server, args.token, args.user, args.password, args.worker_id, job, args.poll)
                except Exception as exc:
                    print(f"任务异常：{exc}", file=sys.stderr, flush=True)
                    try:
                        update(args.server, job["id"], args.worker_id, args.token, args.user, args.password,
                               status="paused", message=f"桌面助手异常：{exc}")
                    except Exception:
                        pass
            else:
                time.sleep(args.poll)
        except KeyboardInterrupt:
            print("桌面助手已停止。", flush=True)
            return 0
        except Exception as exc:
            print(f"云端连接失败：{exc}", file=sys.stderr, flush=True)
            time.sleep(max(args.poll, 10))


if __name__ == "__main__":
    raise SystemExit(main())
