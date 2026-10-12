import tempfile
import unittest
from pathlib import Path

from app.core.dispatch import (DispatchError, action_card, advance_plan,
                               create_plan, get_plan, set_check)


class DispatchWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "dispatch.sqlite3"
        self.data = {
            "workflow": "customer",
            "activity_name": "双线质差测试活动",
            "group_name": "测试客户群",
            "audience_level": "user",
            "method": "按责任客户经理",
            "expected_count": 20,
            "operator": "测试操作人",
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_required_checks_and_external_statuses(self):
        plan = create_plan(self.data, self.path)
        pid = plan["id"]
        with self.assertRaisesRegex(DispatchError, "M1"):
            advance_plan(pid, "prepared", {}, self.path)
        with self.assertRaisesRegex(DispatchError, "M8"):
            set_check(pid, "M8", True, path=self.path)
        for code in ("M1", "M2", "M3", "M4", "M6"):
            set_check(pid, code, True, path=self.path)
        with self.assertRaisesRegex(DispatchError, "M5"):
            set_check(pid, "M5", True, evidence="18", path=self.path)
        set_check(pid, "M5", True, evidence="20", path=self.path)
        self.assertEqual(advance_plan(pid, "prepared", {}, self.path)["status"], "prepared")
        with self.assertRaisesRegex(DispatchError, "顺序"):
            advance_plan(pid, "executed", {}, self.path)
        with self.assertRaisesRegex(DispatchError, "external_activity_id"):
            advance_plan(pid, "submitted", {}, self.path)
        advance_plan(pid, "submitted", {"external_activity_id": "ACT-123"}, self.path)
        advance_plan(pid, "approved", {}, self.path)
        advance_plan(pid, "executed", {}, self.path)
        with self.assertRaisesRegex(DispatchError, "M8"):
            advance_plan(pid, "refreshed", {}, self.path)
        set_check(pid, "M8", True, path=self.path)
        advance_plan(pid, "refreshed", {}, self.path)
        with self.assertRaisesRegex(DispatchError, "note"):
            advance_plan(pid, "verified", {"dispatched_count": 18}, self.path)
        done = advance_plan(pid, "verified", {"dispatched_count": 18,
                                               "note": "两条记录无责任人"}, self.path)
        self.assertEqual(done["dispatched_count"], 18)
        self.assertEqual(done["status"], "verified")
        self.assertIn("ACT-123", action_card(done))
        self.assertGreaterEqual(len(get_plan(pid, self.path)["events"]), 14)

    def test_number_workflow_rejects_invalid_configuration(self):
        bad = {**self.data, "workflow": "number", "audience_level": "user"}
        with self.assertRaisesRegex(DispatchError, "层级"):
            create_plan(bad, self.path)
        bad = {**self.data, "workflow": "number", "audience_level": "customer",
               "method": "到认领包区（直销）"}
        with self.assertRaisesRegex(DispatchError, "方式"):
            create_plan(bad, self.path)
        bad["method"] = "按机构"
        with self.assertRaisesRegex(DispatchError, "下发组织"):
            create_plan(bad, self.path)
        bad["organization"] = "测试机构"
        self.assertEqual(create_plan(bad, self.path)["workflow"], "number")


if __name__ == "__main__":
    unittest.main()
