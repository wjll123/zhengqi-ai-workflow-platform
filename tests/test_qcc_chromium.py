import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch
from urllib.parse import quote

from app.core.browser import BrowserInfo
from app.qcc.chromium import ChromiumSession, browser_status, is_qcc_page
from app.qcc.errors import BrowserActionRequired, QueryError
from app.qcc.jobs import QueryJobs


def tab(identifier, url, port=9222):
    return {"id": identifier, "type": "page", "url": url,
            "webSocketDebuggerUrl": f"ws://127.0.0.1:{port}/devtools/page/{identifier}"}


FIRM = "https://m.qcc.com/firm/" + "a" * 32 + ".html"
SEARCH = "https://www.qcc.com/web/search?key=甲有限公司"
DETAIL = "window.__INITIAL_STATE__=" + json.dumps({"company": {"companyDetail": {
    "Name": "甲有限公司", "KeyNo": "a" * 32, "CreditCode": "A12345678901234567",
    "Partners": [], "Employees": [], "CountInfo": {"PartnersCount": 0, "EmployeesCount": 0}}}}, ensure_ascii=False)
RESULTS = "window.__INITIAL_STATE__=" + json.dumps({"search": {"searchRes": {
    "Status": 200, "Result": [{"Name": "甲有限公司", "KeyNo": "a" * 32,
                                "CreditCode": "A12345678901234567"}]}}}, ensure_ascii=False)


class BrowserSelectionTests(unittest.TestCase):
    def test_only_exact_qcc_hosts_are_accepted(self):
        self.assertTrue(is_qcc_page(tab("good", "https://www.qcc.com/")))
        for url in ("https://www.qcc.com.evil.test/", "https://evil.test/?next=qcc.com",
                    "http://www.qcc.com/", "https://qcc.com.evil.test/"):
            with self.subTest(url=url):
                self.assertFalse(is_qcc_page(tab("bad", url)))

    def test_port_must_belong_to_selected_browser(self):
        with patch("app.qcc.chromium.check_debug_port", return_value=BrowserInfo(version="Edg/130.0", ok=True)), \
             patch("app.qcc.chromium.list_pages", return_value=[tab("edge", FIRM, 9223)]):
            self.assertFalse(browser_status("chrome", 9223)["ready"])
            self.assertTrue(browser_status("edge", 9223)["ready"])

    def test_reuses_saved_tab_id_instead_of_first_qcc_tab(self):
        pages = [tab("other", FIRM), tab("saved", "https://www.qcc.com/web/search?key=old")]
        with patch("app.qcc.chromium.check_debug_port", return_value=BrowserInfo(version="Chrome/130.0", ok=True)), \
             patch("app.qcc.chromium.list_pages", return_value=pages), \
             patch("app.qcc.chromium.CDP") as cdp:
            with ChromiumSession(window_id="saved") as session:
                self.assertEqual(session.window_id, "saved")
            cdp.assert_called_once_with(pages[1]["webSocketDebuggerUrl"])

    def test_new_batch_requires_one_qcc_tab(self):
        pages = [tab("first", FIRM), tab("second", "https://www.qcc.com/")]
        with patch("app.qcc.chromium.check_debug_port", return_value=BrowserInfo(version="Chrome/130.0", ok=True)), \
             patch("app.qcc.chromium.list_pages", return_value=pages):
            self.assertFalse(browser_status("chrome", 9222)["ready"])
            with self.assertRaisesRegex(BrowserActionRequired, "只保留一个"):
                ChromiumSession().__enter__()

    def test_saved_tab_can_be_on_verification_page(self):
        pages = [tab("saved", "https://www.qcckyc.com/verify")]
        with patch("app.qcc.chromium.check_debug_port", return_value=BrowserInfo(version="Chrome/130.0", ok=True)), \
             patch("app.qcc.chromium.list_pages", return_value=pages), \
             patch("app.qcc.chromium.CDP"):
            with ChromiumSession(window_id="saved") as session:
                self.assertEqual(session.window_id, "saved")

    def test_missing_or_unsafe_connection_requires_action(self):
        with patch("app.qcc.chromium.check_debug_port", return_value=BrowserInfo(ok=False)):
            with self.assertRaisesRegex(BrowserActionRequired, "端口"):
                ChromiumSession().__enter__()
        with patch("app.qcc.chromium.check_debug_port", return_value=BrowserInfo(version="Chrome/130.0", ok=True)), \
             patch("app.qcc.chromium.list_pages", return_value=[{
                 **tab("bad", FIRM), "webSocketDebuggerUrl": "ws://example.com:9222/devtools/page/bad"}]), \
             self.assertRaisesRegex(BrowserActionRequired, "已拒绝连接"):
            ChromiumSession().__enter__()


class ChromiumQueryTests(unittest.TestCase):
    def test_search_and_detail_navigation_reuse_one_tab(self):
        session = ChromiumSession(window_id="saved")
        session.cdp = Mock()
        session.before_navigation = Mock()
        with patch.object(session, "_page", side_effect=[("about:blank", ""), (SEARCH, RESULTS), (FIRM, DETAIL)]):
            result = session.query("甲有限公司")
        self.assertEqual(result["company"], "甲有限公司")
        self.assertEqual(session.before_navigation.call_count, 2)
        self.assertEqual([call.args[1]["url"] for call in session.cdp.call.call_args_list],
                         ["https://www.qcc.com/web/search?key=" + quote("甲有限公司"), FIRM])

    def test_verification_page_blocks_without_navigation(self):
        session = ChromiumSession(window_id="saved")
        session.cdp = Mock()
        session.cdp.eval_js.return_value = json.dumps({"url": "https://www.qcc.com/verify",
            "title": "用户验证", "html": "<html><title>用户验证</title></html>"})
        with self.assertRaises(BrowserActionRequired):
            session.query("甲有限公司")
        self.assertTrue(session.preserve_window)
        session.cdp.call.assert_not_called()


class JobStartupTests(unittest.TestCase):
    def test_unavailable_debug_browser_marks_job_blocked(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "names.csv").write_text("公司名称\n甲有限公司\n")
            jobs = QueryJobs(root / "state", inbox, root / "outputs")
            fake = Mock()
            fake.window_id = None
            fake.__enter__ = Mock(side_effect=BrowserActionRequired("Chrome 调试端口不可用"))
            fake.__exit__ = Mock()
            with patch("app.qcc.jobs.ChromiumSession", return_value=fake):
                started = jobs.start({"source_name": "names.csv", "browser": "chrome"})
                for _ in range(100):
                    current = jobs.get(started["id"])
                    if current["status"] != "running":
                        break
                    time.sleep(.01)
            self.assertEqual(current["status"], "blocked")
            self.assertEqual(current["rows"][0]["status"], "blocked")
            self.assertIn("调试端口不可用", current["message"])

    def test_browser_arguments_are_validated(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            inbox = root / "inbox"
            inbox.mkdir()
            (inbox / "names.csv").write_text("公司名称\n甲有限公司\n")
            jobs = QueryJobs(root / "state", inbox, root / "outputs")
            for params in ({"browser": "firefox"}, {"browser": "chrome", "debug_port": "9222"},
                           {"browser": "edge", "debug_port": 80}):
                with self.subTest(params=params), self.assertRaises(QueryError):
                    jobs.start({"source_name": "names.csv", **params})


if __name__ == "__main__":
    unittest.main()
