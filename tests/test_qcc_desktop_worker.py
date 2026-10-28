import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.qcc.jobs import QueryJobs


class DesktopQueueTests(unittest.TestCase):
    def test_desktop_job_can_be_claimed_and_updated(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "names.csv").write_text("公司名称\n甲有限公司\n乙有限公司\n")
            jobs = QueryJobs(root / "state", inbox, root / "outputs")
            queued = jobs.start({"source_name": "names.csv", "execution": "desktop"})
            self.assertEqual(queued["status"], "queued")
            claimed = jobs.claim_worker("mac-worker")
            self.assertEqual(claimed["id"], queued["id"])
            self.assertEqual(claimed["worker_id"], "mac-worker")
            update = jobs.worker_update(queued["id"], "mac-worker", {
                "status": "paused", "message": "需要人工验证", "rows": claimed["rows"]})
            self.assertFalse(update["stop"])
            self.assertEqual(update["job"]["status"], "paused")

    def test_worker_cannot_update_another_worker_job(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "names.csv").write_text("公司名称\n甲有限公司\n")
            jobs = QueryJobs(root / "state", inbox, root / "outputs")
            queued = jobs.start({"source_name": "names.csv", "execution": "desktop"})
            jobs.claim_worker("mac-worker")
            with self.assertRaisesRegex(ValueError, "不属于"):
                jobs.worker_update(queued["id"], "other-worker", {"status": "paused"})


if __name__ == "__main__":
    unittest.main()
