import json
import tempfile
import unittest
from pathlib import Path

from app.core.dispatch_rpa import RPAError, _stage_steps, readiness, run_steps


class FakeCDP:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def eval_js(self, code):
        self.calls.append(code)
        return next(self.responses)


class DispatchRPATests(unittest.TestCase):
    def test_unconfigured_mode_stays_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "config.json"
            path.write_text(json.dumps({"version": 1, "enabled": False,
                                        "workflows": {"customer": {"prepare_submit": []}}}))
            result = readiness("customer", "prepare_submit", path)
            self.assertFalse(result["ready"])
            self.assertIn("未启用", result["message"])

    def test_steps_are_strict_and_stop_on_first_failure(self):
        steps = [{"action": "fill", "selector": "#activity", "field": "activity_name"},
                 {"action": "click", "selector": "#submit"}]
        self.assertEqual(_stage_steps({"workflows": {"customer": {"prepare_submit": steps}}},
                                      "customer", "prepare_submit"), steps)
        cdp = FakeCDP([{"ok": True}, {"ok": False, "message": "匹配元素数 0"}])
        with self.assertRaisesRegex(RPAError, "第 2 步"):
            run_steps(cdp, {"activity_name": "测试活动"}, steps)
        self.assertEqual(len(cdp.calls), 2)
        self.assertIn("\\u6d4b\\u8bd5\\u6d3b\\u52a8", cdp.calls[0])
        with self.assertRaisesRegex(RPAError, "选择器"):
            _stage_steps({"workflows": {"number": {"execute_refresh": [
                {"action": "click", "selector": ""}]} }}, "number", "execute_refresh")


if __name__ == "__main__":
    unittest.main()
