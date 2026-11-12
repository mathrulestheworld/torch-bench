import unittest

from helpers import fixture
from torchbench import slurm


class Durations(unittest.TestCase):
    def test_durations(self):
        self.assertEqual(slurm.parse_duration("1-02:03:04"), 93784)
        self.assertEqual(slurm.parse_duration("02:03:04"), 7384)
        self.assertEqual(slurm.parse_duration("5:52"), 352)
        self.assertEqual(slurm.parse_duration("0:00"), 0)
        self.assertIsNone(slurm.parse_duration("UNLIMITED"))
        self.assertIsNone(slurm.parse_duration("N/A"))

    def test_times(self):
        self.assertIsNotNone(slurm.parse_time("2026-11-10T19:18:00"))
        for junk in ("Unknown", "None", "N/A", ""):
            self.assertIsNone(slurm.parse_time(junk))

    def test_human(self):
        self.assertEqual(slurm.human_duration(40), "40 s")
        self.assertEqual(slurm.human_duration(18 * 60), "18 min")
        self.assertEqual(slurm.human_duration(6.5 * 3600), "6.5 h")
        self.assertEqual(slurm.human_duration(3 * 3600), "3 h")
        self.assertEqual(slurm.human_duration(2 * 86400), "2 days")

    def test_array_spec(self):
        self.assertEqual(slurm.array_spec_count("[4-18%4]"), (15, 4))
        self.assertEqual(slurm.array_spec_count("[0-2,4-15,17-18%4]"), (17, 4))
        self.assertEqual(slurm.array_spec_count("[1,3,5-7]"), (5, None))
        self.assertEqual(slurm.array_spec_count("[0-9:2]"), (5, None))
        self.assertEqual(slurm.split_job_id("123_[5-18%4]"), ("123", "[5-18%4]"))
        self.assertEqual(slurm.split_job_id("123"), ("123", ""))

    def test_gpu_count(self):
        self.assertEqual(slurm.gpu_count("billing=8,cpu=8,gres/gpu:l40s=1,gres/gpu=1,mem=96G,node=1"), 1)
        self.assertEqual(slurm.gpu_count("gres/gpu:l40s:2"), 2)
        self.assertEqual(slurm.gpu_count("gpu:l40s:4(S:0-1)"), 4)
        self.assertEqual(slurm.gpu_count("gpu:8"), 8)
        self.assertEqual(slurm.gpu_count("(null)"), 0)
        self.assertEqual(slurm.gpu_count("N/A"), 0)


class Squeue(unittest.TestCase):
    def setUp(self):
        self.parsed = slurm.parse_squeue(fixture("squeue.txt"))
        self.jobs = {j["id"]: j for j in self.parsed["jobs"]}

    def test_all_lines_read(self):
        self.assertEqual(self.parsed["skipped"], 0)
        self.assertEqual(len(self.jobs), 8)

    def test_running_task(self):
        j = self.jobs["22601412_2"]
        self.assertEqual((j["kind"], j["state_word"], j["array_id"], j["task"]), ("running", "Running", "22601412", "2"))
        self.assertEqual((j["elapsed_s"], j["limit_s"], j["gpus"], j["nodelist"]), (41 * 60 + 7, 4 * 3600, 1, "gl021"))

    def test_pending_array(self):
        j = self.jobs["22601412_[4-18%4]"]
        self.assertEqual((j["kind"], j["reason"], j["task_count"], j["array_limit"]), ("waiting", "JobArrayTaskLimit", 15, 4))
        self.assertIsNone(j["start_ts"])

    def test_scheduler_estimate_and_pipe_in_name(self):
        self.assertIsNotNone(self.jobs["22601977"]["start_ts"])
        self.assertEqual(self.jobs["22602050"]["name"], "tokenize|corpus")
        self.assertEqual(self.jobs["22602050"]["gpus"], 0)

    def test_junk_is_skipped_not_guessed(self):
        parsed = slurm.parse_squeue("slurm_load_jobs error: Socket timed out\n22601977|PENDING\n")
        self.assertEqual((parsed["jobs"], parsed["skipped"]), ([], 2))

    def test_grouping(self):
        groups = {g["id"]: g for g in slurm.group_jobs(self.parsed["jobs"])}
        train = groups["22601412"]
        self.assertTrue(train["is_array"])
        self.assertEqual(train["counts"], {"running": 4, "waiting": 15})
        self.assertEqual((train["total"], train["kind"], train["array_limit"]), (19, "running", 4))
        self.assertEqual(groups["22601977"]["kind"], "waiting")


class Sacct(unittest.TestCase):
    def setUp(self):
        self.parsed = slurm.parse_sacct(fixture("sacct.txt"))
        self.jobs = {j["id"]: j for j in self.parsed["jobs"]}

    def test_all_lines_read(self):
        self.assertEqual(self.parsed["skipped"], 0)
        self.assertEqual(len(self.jobs), 18)

    def test_states(self):
        self.assertEqual(self.jobs["22590102_2"]["kind"], "failed")
        self.assertEqual(self.jobs["22590102_3"]["state_word"], "Ran out of time")
        self.assertEqual(self.jobs["22596400"]["state_word"], "Ran out of memory")
        c = self.jobs["22595540"]
        self.assertEqual((c["state"], c["kind"], c["state_note"]), ("CANCELLED", "cancelled", "by 3412345"))
        self.assertIsNone(c["start_ts"])

    def test_fields(self):
        j = self.jobs["22590101"]
        self.assertEqual((j["partition"], j["gpus"], j["elapsed_s"], j["exit_code"]), ("l40s_public", 1, 10200, "0:0"))
        self.assertAlmostEqual(j["start_ts"] - j["eligible_ts"], 600, delta=1)
        self.assertEqual(self.jobs["22599001"]["name"], "tokenize|corpus")
        self.assertEqual(self.jobs["22601412_[4-18%4]"]["task_count"], 15)

    def test_without_header(self):
        body = "\n".join(fixture("sacct.txt").splitlines()[1:])
        self.assertEqual(len(slurm.parse_sacct(body)["jobs"]), 18)


class Others(unittest.TestCase):
    def test_sshare(self):
        rows = slurm.parse_sshare(fixture("sshare.txt"))["rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["account"], rows[0]["user"]), ("torch_pr_000_general", "abc1234"))
        self.assertAlmostEqual(rows[0]["fairshare"], 0.118421)

    def test_sprio(self):
        parsed = slurm.parse_sprio(fixture("sprio.txt"))
        self.assertEqual(parsed["skipped"], 0)
        self.assertEqual(set(parsed["jobs"]), {"22601412", "22601413", "22601977"})
        self.assertEqual(parsed["jobs"]["22601977"]["fairshare"], 1184)
        self.assertEqual(parsed["jobs"]["22601977"]["priority"], 8421)

    def test_sinfo(self):
        parts = slurm.parse_sinfo(fixture("sinfo.txt"))["partitions"]
        l40s = parts["l40s_public"]
        self.assertEqual((l40s["nodes"], l40s["gpus_total"], l40s["gpus_used"], l40s["gpus_free"]), (6, 24, 13, 7))
        self.assertEqual(l40s["nodes_usable"], 4)        # the drained and fully allocated machines do not count
        self.assertEqual(parts["h200_public"]["gpus_free"], 0)
        self.assertEqual((parts["cs"]["gpus_total"], parts["cs"]["nodes_usable"]), (0, 2))

    def test_myquota(self):
        q = slurm.parse_myquota(fixture("myquota.txt"))
        self.assertTrue(q["parsed"])
        fs = {f["fs"]: f for f in q["filesystems"]}
        self.assertEqual(set(fs), {"/home", "/scratch", "/archive"})
        s = fs["/scratch"]
        self.assertEqual((s["space_pct"], s["files_pct"], s["space_limit"], s["files_limit"]), (92.26, 48.74, "5.0TB", "5.0M"))
        self.assertEqual((s["backed_up"], s["flushed"], s["worst_pct"]), (False, True, 92.26))
        self.assertTrue(fs["/archive"]["backed_up"])

    def test_myquota_unreadable_shows_raw(self):
        q = slurm.parse_myquota("myquota: command changed its output\nscratch 4.6T used\n")
        self.assertFalse(q["parsed"])
        self.assertIn("command changed", q["raw"])

    def test_scontrol(self):
        f = slurm.parse_scontrol_job(fixture("scontrol.txt"))
        self.assertEqual((f["JobId"], f["JobName"], f["JobState"], f["Reason"]), ("22601977", "probe-sweep-h200", "PENDING", "Priority"))
        self.assertEqual(f["Command"], "/scratch/abc1234/demo-project/scripts/run_probe_sweep_slurm.sh sweep h200")
        self.assertEqual(f["TresPerNode"], "gres/gpu:h200:1")


if __name__ == "__main__":
    unittest.main()
