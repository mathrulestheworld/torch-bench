import os
import unittest

from helpers import FakeTorchCase
from torchbench import reasons
from torchbench.cmdlog import CommandLog
from torchbench.dashboard import Dashboard
from torchbench.ssh import SSH
from torchbench.waits import WaitHistory


class Reasons(unittest.TestCase):
    def test_priority_with_low_fairshare_is_explained(self):
        why = reasons.explain("Priority", fairshare=0.12, typical_wait="6.5 h")
        self.assertIn("used a lot of GPU hours recently", why["detail"])
        self.assertIn("0.12", why["detail"])
        self.assertIn("about 6.5 h", why["detail"])
        self.assertIn("estimate", why["detail"])
        self.assertFalse(why["stuck"])

    def test_priority_with_good_fairshare_does_not_blame_usage(self):
        self.assertNotIn("used a lot", reasons.explain("Priority", fairshare=0.8)["detail"])

    def test_common_reasons(self):
        self.assertIn("earlier job", reasons.explain("Dependency")["title"])
        self.assertIn("4 tasks at once", reasons.explain("JobArrayTaskLimit", array_limit=4)["detail"])
        self.assertIn("free machine", reasons.explain("Resources")["title"])
        self.assertIn("usage limit", reasons.explain("QOSMaxGRESPerUser")["title"])
        self.assertIn("unavailable", reasons.explain("ReqNodeNotAvail, Reserved for maintenance")["title"])

    def test_stuck_jobs_are_flagged(self):
        for r in ("DependencyNeverSatisfied", "JobHeldUser", "PartitionTimeLimit", "JobHeldAdmin"):
            self.assertTrue(reasons.explain(r)["stuck"], r)

    def test_unknown_reason_is_shown_not_hidden(self):
        self.assertIn("SomethingNew", reasons.explain("SomethingNew")["detail"])

    def test_fairshare_words(self):
        self.assertEqual(reasons.fairshare_words(0.9)["level"], "good")
        self.assertEqual(reasons.fairshare_words(0.3)["level"], "middle")
        self.assertEqual(reasons.fairshare_words(0.1)["level"], "low")
        self.assertEqual(reasons.fairshare_words(None)["level"], "unknown")


class DashboardData(FakeTorchCase):
    def setUp(self):
        super().setUp()
        self.ssh = SSH("torch", CommandLog())
        self.dash = Dashboard(self.config(), self.ssh, WaitHistory())

    def load(self, force=False):
        self.dash.kick(force=force, wait=True)
        return self.dash.snapshot()

    def test_connected_dashboard(self):
        self.open_login()
        d = self.load()
        self.assertTrue(d["connected"])
        self.assertEqual(d["summary"]["running"], 5)
        self.assertEqual(d["summary"]["waiting"], 17)
        self.assertEqual(d["summary"]["headline"], "5 jobs are running and 17 are waiting.")
        self.assertEqual(d["summary"]["failed"], 3)       # failed, ran out of time, ran out of memory
        self.assertIn("3 jobs failed in the last 7 days", d["summary"]["subline"])
        for name, source in d["sources"].items():
            self.assertEqual(source["error"], "", name)
            self.assertTrue(source["loaded"], name)

    def test_pending_array_has_a_plain_reason_and_fairshare_is_shown(self):
        self.open_login()
        d = self.load()
        groups = {g["id"]: g for g in d["queue"]}
        train = groups["22601412"]
        self.assertEqual(train["counts"], {"running": 4, "waiting": 15})
        self.assertEqual(train["why"]["title"], "Waiting its turn in the batch")
        self.assertIn("4 tasks at once", train["why"]["detail"])
        self.assertIn("earlier job", groups["22601413"]["why"]["title"])
        h200 = groups["22601977"]
        self.assertIn("used a lot of GPU hours recently", h200["why"]["detail"])
        self.assertIn("scheduler's current estimate", h200["scheduler_estimate"])
        self.assertEqual(h200["priority"]["fairshare"], 1184)
        self.assertAlmostEqual(d["fairshare"]["value"], 0.118421)
        self.assertEqual((d["fairshare"]["level"], d["fairshare"]["account"]), ("low", "torch_pr_000_general"))
        self.assertEqual([g["kind"] for g in d["queue"]][:2], ["running", "running"])   # running first

    def test_history_groups(self):
        self.open_login()
        groups = {g["id"]: g for g in self.load()["history"]}
        self.assertNotIn("22601412", groups)                # still running or waiting: not "finished"
        test = groups["22590102"]
        self.assertEqual(test["counts"], {"done": 2, "failed": 2})
        self.assertEqual(test["kind"], "failed")
        self.assertEqual(groups["22595540"]["kind"], "cancelled")
        self.assertEqual(groups["22599001"]["name"], "tokenize|corpus")

    def test_wait_estimates_and_free_gpus(self):
        self.open_login()
        parts = {p["partition"]: p for p in self.load()["partitions"]}
        l40s, h200, cs = parts["l40s_public"], parts["h200_public"], parts["cs"]
        self.assertEqual((l40s["gpus_free"], l40s["gpus_total"], l40s["role"]), (7, 24, "GPU"))
        self.assertEqual(l40s["typical_wait"], "18 min")
        self.assertEqual(h200["typical_wait"], "6.5 h")
        self.assertEqual(h200["gpus_free"], 0)
        self.assertEqual((cs["role"], cs["nodes_usable"], cs["typical_wait"]), ("CPU", 2, "60 s"))

    def test_quota_warning_above_90_percent(self):
        self.open_login()
        q = self.load()["quota"]
        self.assertTrue(q["parsed"])
        self.assertEqual(len(q["warnings"]), 1)
        self.assertIn("/scratch is 92% full", q["warnings"][0])

    def test_losing_the_login_degrades_within_one_refresh(self):
        self.open_login()
        self.assertTrue(self.load()["connected"])
        before = len(self.remote_commands())
        self.close_login()                                   # the ControlMaster dies
        d = self.load(force=True)
        self.assertFalse(d["connected"])
        self.assertEqual(len(self.remote_commands()), before)   # nothing was sent after the login went
        self.assertEqual(d["summary"]["running"], 5)            # the last answer is still shown, marked stale
        self.assertTrue(d["sources"]["queue"]["loaded"])

    def test_never_connected(self):
        d = self.load()
        self.assertFalse(d["connected"])
        self.assertEqual(d["queue"], [])
        self.assertEqual(self.remote_commands(), [])

    def test_polite_refresh_only_asks_what_is_stale(self):
        self.open_login()
        self.load()
        first = self.remote_commands()
        self.assertEqual([c.split()[0] for c in first], ["squeue", "sacct", "sshare", "sinfo", "myquota", "sprio"])
        self.load()                                          # straight away again: everything is fresh
        self.assertEqual(self.remote_commands(), first)

    def test_one_failing_source_does_not_blank_the_rest(self):
        self.open_login()
        os.environ["FAKE_SSH_FAIL"] = "sshare"
        d = self.load()
        self.assertIn("simulated failure", d["sources"]["fairshare"]["error"])
        self.assertIsNone(d["fairshare"]["value"])
        self.assertEqual(d["summary"]["running"], 5)

    def test_job_detail_and_disk(self):
        self.open_login()
        detail = self.dash.job_detail("22601977")
        self.assertTrue(detail["ok"])
        self.assertIn({"key": "Reason", "value": "Priority"}, detail["fields"])
        disk = self.dash.measure_disk()
        self.assertEqual((disk["ok"], disk["size"]), (True, "1.9T"))
        self.close_login()
        self.assertTrue(self.dash.job_detail("22601977")["not_connected"])


if __name__ == "__main__":
    unittest.main()
