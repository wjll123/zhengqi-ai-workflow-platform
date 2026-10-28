"""SQLite 持久队列：单个浏览器工作者、逐家提交、进程中断后续查。"""
import json
import math
import sqlite3
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from ..core.config import INBOX_DIR, OUTPUT_DIR, STATE_DIR
from .errors import BrowserActionRequired, QueryBusy, QueryError, QueryPaused
from .fields import SCHEMA_VERSION, is_full
from .pacing import InterfacePacer, NavigationPacer
from .safari import SafariSession
from .chromium import ChromiumSession
from .sources import read_codes, read_names
from .workbooks import export_job, read_previous


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class QueryJobs:
    def __init__(self, root=STATE_DIR / "qcc", inbox=INBOX_DIR, output=OUTPUT_DIR, session_factory=SafariSession):
        self.root, self.inbox, self.output = map(Path, (root, inbox, output))
        self.session_factory = session_factory

    @contextmanager
    def _db(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.root / "jobs.sqlite3", timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, payload TEXT NOT NULL, stop INTEGER NOT NULL DEFAULT 0, updated TEXT NOT NULL)")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _lock(self):
        if sys.platform != "darwin":
            raise QueryError("自动查询只能在 Mac 本机运行，云端无法连接本机浏览器。")
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True)
        lock = (self.root / "safari.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise QueryBusy("已有浏览器查询在运行，请先暂停该批次。")
        return lock

    def _save(self, job):
        job["updated_at"] = now()
        with self._db() as db:
            db.execute("INSERT INTO jobs (id,payload,updated) VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated",
                       (job["id"], json.dumps(job, ensure_ascii=False, allow_nan=False), job["updated_at"]))

    def _read(self, job_id):
        with self._db() as db:
            row = db.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise QueryError("查询批次不存在。")
        return json.loads(row[0])

    def _recover(self):
        with self._db() as db:
            rows = db.execute("SELECT id,payload FROM jobs").fetchall()
            for row in rows:
                job = json.loads(row["payload"])
                if job["status"] != "running":
                    continue
                job.update(status="paused", current_name="", message="运行已中断，已保存结果可下载或继续。")
                for item in job["rows"]:
                    if item["status"] == "querying":
                        item["status"] = "pending"
                db.execute("UPDATE jobs SET payload=? WHERE id=?", (json.dumps(job, ensure_ascii=False), job["id"]))

    def _recover_if_idle(self):
        if sys.platform != "darwin":
            return
        try:
            lock = self._lock()
        except QueryBusy:
            return
        try:
            self._recover()
        finally:
            lock.close()

    @staticmethod
    def _public(job):
        result = dict(job)
        result.pop("identity_codes", None)
        result.pop("window_id", None)
        result["counts"] = {key: sum(row["status"] == key for row in job["rows"])
                            for key in ("success", "failed", "pending", "blocked", "querying")}
        result["counts"]["total"] = len(job["rows"])
        result["counts"]["full"] = sum(row["status"] == "success" and is_full(row.get("data")) for row in job["rows"])
        result["counts"]["retained"] = sum(bool(row.get("data")) and not is_full(row.get("data")) for row in job["rows"])
        result["rows"] = [{k: v for k, v in row.items() if k != "previous_data"} for row in job["rows"]]
        for row in result["rows"]:
            if row.get("data"):
                row["data"] = {k: v for k, v in row["data"].items() if k != "raw_records"}
        return result

    @classmethod
    def _worker_public(cls, job):
        result = cls._public(job)
        result["identity_codes"] = dict(job.get("identity_codes", {}))
        return result

    def get(self, job_id):
        self._recover_if_idle()
        return self._public(self._read(job_id))

    def recent(self):
        self._recover_if_idle()
        with self._db() as db:
            rows = db.execute("SELECT payload FROM jobs ORDER BY updated DESC,rowid DESC LIMIT 30").fetchall()
        return [self._public(json.loads(row[0])) for row in rows]

    def start(self, params):
        if not isinstance(params, dict):
            raise QueryError("请提交名单与查询设置。")
        limit, delay = params.get("max_new_queries", 100), params.get("delay_seconds", 60)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise QueryError("每轮数量必须是 1 到 100 的整数。")
        if type(delay) not in (int, float) or not math.isfinite(delay) or not 60 <= delay <= 600:
            raise QueryError("每次页面访问间隔必须为 60 到 600 秒。")
        for key in ("source_name", "resume_from", "identity_source", "browser"):
            if not isinstance(params.get(key, ""), str):
                raise QueryError("文件名参数不正确。")
        source = params.get("source_name", "")
        browser = params.get("browser", "safari").lower()
        if browser not in {"safari", "chrome", "edge"}:
            raise QueryError("浏览器必须选择 Safari、Chrome 或 Edge。")
        debug_port = params.get("debug_port", 9222)
        if type(debug_port) is not int or not 1024 <= debug_port <= 65535:
            raise QueryError("浏览器调试端口必须是 1024 到 65535 的整数。")
        execution = params.get("execution", "local")
        if execution not in {"local", "desktop"}:
            raise QueryError("运行位置必须选择本机或桌面助手。")
        names = read_names(source, self.inbox)
        previous = params.get("resume_from", "")
        successes = read_previous(previous, names, self.output) if previous else {}
        identity_source = params.get("identity_source", "")
        codes = read_codes(identity_source, names, self.inbox) if identity_source else {}
        job = {"id": uuid.uuid4().hex, "source_name": source, "identity_source": identity_source,
               "created_at": now(), "execution": execution,
               "status": "queued" if execution == "desktop" else "running",
               "message": "等待桌面助手领取任务。" if execution == "desktop" else "正在连接查询浏览器。",
               "current_name": "",
               "max_new_queries": limit, "delay_seconds": delay, "browser": browser,
               "debug_port": debug_port, "identity_codes": codes,
               "window_id": None, "rows": [successes.get(name) or
                   {"name": name, "status": "pending", "error": ""} for name in names]}
        self._prepare_full(job)
        if execution == "desktop":
            self._save(job)
            return self.get(job["id"])
        lock = self._lock()
        try:
            self._recover()
            self._launch(job, lock)
        except Exception:
            lock.close()
            raise
        return self.get(job["id"])

    def _launch(self, job, lock):
        self._save(job)
        threading.Thread(target=self._run, args=(job, lock), daemon=True,
                         name="qcc-" + job["id"][:8]).start()

    def resume(self, job_id):
        existing = self._read(job_id)
        if existing.get("execution") == "desktop":
            self._prepare_full(existing)
            if all(row["status"] == "success" for row in existing["rows"]):
                raise QueryError("该批次全部成功，无需重复查询。")
            for row in existing["rows"]:
                if row["status"] != "success":
                    row.update(status="pending", error="")
            existing.update(status="queued", worker_id="", worker_heartbeat_at=0,
                            message="已重新排队，等待桌面助手领取。", current_name="")
            with self._db() as db:
                db.execute("UPDATE jobs SET stop=0 WHERE id=?", (job_id,))
            self._save(existing)
            return self.get(job_id)
        lock = self._lock()
        try:
            self._recover()
            job = self._read(job_id)
            self._prepare_full(job)
            if all(row["status"] == "success" for row in job["rows"]):
                raise QueryError("该批次全部成功，无需重复查询。")
            for row in job["rows"]:
                if row["status"] != "success":
                    row.update(status="pending", error="")
            job.update(status="running", message="正在继续未完成企业。", current_name="")
            with self._db() as db:
                db.execute("UPDATE jobs SET stop=0 WHERE id=?", (job_id,))
            self._launch(job, lock)
        except Exception:
            lock.close()
            raise
        return self.get(job_id)

    @staticmethod
    def _prepare_full(job):
        job["schema_version"] = SCHEMA_VERSION
        job["delay_seconds"] = max(60, job.get("delay_seconds", 60))
        for row in job["rows"]:
            if row.get("data") and not is_full(row["data"]):
                row.setdefault("previous_data", row["data"])
                row.setdefault("previous_queried_at", row.get("queried_at", ""))
                row.update(status="pending", note="已保留旧版基础字段，等待补齐详情页字段。", error="")

    def pause(self, job_id):
        job = self.get(job_id)
        if job["status"] == "running":
            with self._db() as db:
                db.execute("UPDATE jobs SET stop=1 WHERE id=?", (job_id,))
            job["message"] = "已请求暂停。当前企业处理结束后保存进度。"
        return job

    def claim_worker(self, worker_id):
        """Claim one queued desktop job for a named desktop worker."""
        worker_id = str(worker_id or "").strip()
        if not worker_id or len(worker_id) > 120:
            raise QueryError("桌面助手编号无效。")
        now_epoch = time.time()
        with self._db() as db:
            rows = db.execute("SELECT id,payload FROM jobs ORDER BY updated ASC,rowid ASC").fetchall()
            for row in rows:
                job = json.loads(row["payload"])
                if job.get("execution") != "desktop":
                    continue
                if job.get("status") == "running" and now_epoch - float(job.get("worker_heartbeat_at", 0) or 0) > 180:
                    job.update(status="queued", worker_id="", worker_heartbeat_at=0,
                               message="桌面助手连接中断，已重新排队。", current_name="")
                if job.get("status") != "queued":
                    if job.get("status") == "running" and job.get("worker_id") == worker_id:
                        return self._worker_public(job)
                    continue
                job.update(status="running", worker_id=worker_id, worker_heartbeat_at=now_epoch,
                            message="桌面助手已领取任务，正在启动本机浏览器查询。", current_name="")
                db.execute("UPDATE jobs SET payload=?,updated=? WHERE id=?",
                           (json.dumps(job, ensure_ascii=False, allow_nan=False), now(), job["id"]))
                return self._worker_public(job)
        return None

    def worker_job(self, job_id, worker_id):
        job = self._read(job_id)
        if job.get("execution") != "desktop" or job.get("worker_id") != worker_id:
            raise QueryError("任务不属于此桌面助手。")
        return job

    def worker_update(self, job_id, worker_id, update):
        job = self.worker_job(job_id, worker_id)
        if not isinstance(update, dict):
            raise QueryError("桌面助手进度格式不正确。")
        if update.get("status") not in {"running", "blocked", "paused", "completed", "queued"}:
            raise QueryError("桌面助手状态不正确。")
        rows = update.get("rows")
        if rows is not None:
            if not isinstance(rows, list) or len(rows) != len(job["rows"]):
                raise QueryError("桌面助手返回的企业数量不一致。")
            expected = [row["name"] for row in job["rows"]]
            if [row.get("name") for row in rows] != expected:
                raise QueryError("桌面助手返回的企业名单不一致。")
            job["rows"] = rows
        for key in ("status", "message", "current_name", "next_navigation_at"):
            if key in update:
                job[key] = update[key]
        job["worker_heartbeat_at"] = time.time()
        self._save(job)
        with self._db() as db:
            stop = bool(db.execute("SELECT stop FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
        return {"job": self._worker_public(job), "stop": stop}

    def attach_worker_result(self, job_id, worker_id, source_path):
        job = self.worker_job(job_id, worker_id)
        source_path = Path(source_path)
        if not source_path.is_file():
            raise QueryError("桌面助手结果文件不存在。")
        target = self.output / f"企查查-批量查询-{job_id}.xlsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        source_path.replace(target)
        job["result_file"] = target.name
        job["worker_heartbeat_at"] = time.time()
        self._save(job)
        return self._worker_public(job)

    def _stopped(self, job_id):
        with self._db() as db:
            row = db.execute("SELECT stop FROM jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row[0])

    def _wait(self, job):
        end = time.monotonic() + job["delay_seconds"]
        while time.monotonic() < end:
            if self._stopped(job["id"]):
                return
            time.sleep(min(0.5, max(0, end - time.monotonic())))

    def _run(self, job, lock):
        session = None
        attempted = 0
        pacer = None
        try:
            def report(due, spacing):
                when = datetime.fromtimestamp(due).astimezone().strftime("%H:%M:%S")
                job.update(message=f"控制访问频率：最早 {when} 访问下一页（间隔至少 {spacing:g} 秒）。当前企业：{job['current_name']}", next_navigation_at=due)
                self._save(job)
            interface_pacer = InterfacePacer(self.root / "interface.json", 3,
                                             lambda: self._stopped(job["id"]))
            pacer = NavigationPacer(self.root / "navigation.json", 3,
                                    lambda: self._stopped(job["id"]), report)
            pending = [row for row in job["rows"] if row["status"] != "success"]
            if pending:
                if job.get("browser", "safari") == "safari":
                    session = self.session_factory(window_id=job.get("window_id"))
                else:
                    session = ChromiumSession(window_id=job.get("window_id"), browser=job["browser"], debug_port=job["debug_port"])
                session.before_navigation = pacer.before_navigation
                session.before_call = interface_pacer.before_call
                with session:
                    job["window_id"] = session.window_id
                    self._save(job)
                    for row in pending:
                        if attempted >= job["max_new_queries"] or self._stopped(job["id"]):
                            session.preserve_window = True
                            job.update(status="paused", message="已暂停，成功记录已保存，可继续查询。")
                            break
                        if attempted:
                            self._wait(job)
                        if self._stopped(job["id"]):
                            session.preserve_window = True
                            job.update(status="paused", message="已暂停，成功记录已保存，可继续查询。")
                            break
                        row["status"] = "querying"
                        job.update(current_name=row["name"], message="正在查询：" + row["name"])
                        self._save(job)
                        attempted += 1
                        try:
                            options = {"credit_code": job["identity_codes"].get(row["name"], "")}
                            if row.get("data", {}).get("url"):
                                options["detail_url"] = row["data"]["url"]
                            data = session.query(row["name"], **options)
                            row.update(status="success", data=data, error="", note=data.get("match_note", ""), queried_at=now())
                            job.update(message="已保存详情页完整字段：" + row["name"] + "。按设定间隔继续下一家。")
                            job.pop("next_navigation_at", None)
                        except QueryPaused as exc:
                            row.update(status="pending", error="")
                            session.preserve_window = True
                            job.update(status="paused", message=str(exc))
                            break
                        except BrowserActionRequired as exc:
                            pacer.blocked()
                            session.preserve_window = True
                            row.update(status="blocked", error=str(exc))
                            job.update(status="blocked", message=str(exc) + " 续查将遵守冷却时间，并自动增大访问间隔。")
                            self._save(job)
                            break
                        except QueryError as exc:
                            row.update(status="failed", error=str(exc))
                            job.update(message="该企业未完成：" + row["name"] + "。原因：" + str(exc))
                        self._save(job)
            if job["status"] == "running":
                failures = sum(row["status"] == "failed" for row in job["rows"])
                job.update(status="completed", message=f"本轮完成，{failures} 家查询失败，可查看原因后重试。" if failures else "全部企业查询成功。")
        except BrowserActionRequired as exc:
            if session:
                session.preserve_window = True
                job["window_id"] = session.window_id
            first = next((row for row in job["rows"] if row["status"] != "success"), None)
            if first:
                first.update(status="blocked", error=str(exc))
            job.update(status="blocked", message=str(exc))
        except Exception as exc:
            job.update(status="paused", message="查询已暂停：" + str(exc))
            for row in job["rows"]:
                if row["status"] == "querying":
                    row.update(status="pending", error="运行中断，需继续查询。")
        finally:
            job["current_name"] = ""
            if session and not session.preserve_window:
                job["window_id"] = None
            try:
                self._save(job)
            finally:
                lock.close()

    def export(self, job_id):
        job = self._read(job_id)
        if job.get("result_file"):
            path = self.output / job["result_file"]
            if path.is_file():
                return path
        return export_job(self.get(job_id), self.output)


jobs = QueryJobs()
