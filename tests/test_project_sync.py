"""Project locations, push and pull, against the stand-in cluster (a real rsync at both ends)."""
import os
import shutil
import time
import unittest

from helpers import FakeTorchCase
from torchbench import config, project
from torchbench.cmdlog import CommandLog
from torchbench.ssh import SSH
from torchbench.sync import Sync, parse_file_list, parse_stats

HAVE_RSYNC = shutil.which("rsync") is not None


class Parsing(unittest.TestCase):
    def test_file_list_rsync3(self):
        out = ("sending incremental file list\n./\nsrc/\nsrc/train.py\nREADME.md\nname with space.txt\n\n"
               "Number of files: 4 (reg: 3, dir: 1)\nNumber of regular files transferred: 3\n"
               "Total transferred file size: 1,234 bytes\nsent 162 bytes  received 39 bytes  402.00 bytes/sec\n"
               "total size is 12  speedup is 0.06 (DRY RUN)\n")
        self.assertEqual(parse_file_list(out), ["src/train.py", "README.md", "name with space.txt"])
        self.assertEqual(parse_stats(out), {"files": 3, "bytes": 1234})

    def test_file_list_old_macos_rsync(self):
        out = ("building file list ... done\nsrc/\nsrc/train.py\n\nNumber of files: 3\nNumber of files transferred: 1\n"
               "Total transferred file size: 12 bytes\n\nsent 100 bytes  received 26 bytes  252.00 bytes/sec\n")
        self.assertEqual(parse_file_list(out), ["src/train.py"])
        self.assertEqual(parse_stats(out), {"files": 1, "bytes": 12})

    def test_nothing_to_copy(self):
        out = "sending incremental file list\n\nsent 60 bytes  received 12 bytes  144.00 bytes/sec\ntotal size is 12  speedup is 0.17 (DRY RUN)\n"
        self.assertEqual(parse_file_list(out), [])
        self.assertEqual(parse_stats("no stats here"), {"files": None, "bytes": None})


class ProjectSetup(FakeTorchCase):
    def setUp(self):
        super().setUp()
        self.base = config.save(self.config(project_name="", remote_dir="", local_dir=""))
        self.home = self.tmp / "machome"

    def test_defaults_are_desktop_and_scratch(self):
        saved = project.save(self.base, {"project_name": "bird-study"})
        cfg = saved["config"]
        self.assertEqual(cfg["local_dir"], str(self.home / "Desktop" / "bird-study"))
        self.assertEqual(cfg["remote_dir"], "/scratch/abc1234/bird-study")
        self.assertTrue(saved["created_local"])
        self.assertTrue((self.home / "Desktop" / "bird-study").is_dir())           # made on the Mac ...
        self.assertFalse((self.tmp / "fs" / "scratch" / "bird-study").exists())     # ... and nothing made on Torch
        self.assertEqual(config.load()[0]["project_name"], "bird-study")

    def test_both_locations_can_be_chosen(self):
        (self.home / "Research").mkdir()
        saved = project.save(self.base, {"project_name": "bird-study", "local_dir": "~/Research/birds",
                                         "remote_dir": "/archive/abc1234/keep/birds"})
        self.assertEqual(saved["config"]["local_dir"], str(self.home / "Research" / "birds"))
        self.assertEqual(saved["config"]["remote_dir"], "/archive/abc1234/keep/birds")

    def test_existing_folder_is_adopted_not_touched(self):
        existing = self.home / "Desktop" / "old-project"
        existing.mkdir()
        (existing / "keep.txt").write_text("mine")
        saved = project.save(self.base, {"project_name": "old-project"})
        self.assertFalse(saved["created_local"])
        self.assertEqual((existing / "keep.txt").read_text(), "mine")

    def test_bad_locations_are_refused_and_nothing_is_created(self):
        for body in ({"project_name": "bad name!"}, {"project_name": "p", "local_dir": "/etc/p"},
                     {"project_name": "p", "local_dir": "~"}, {"project_name": "p", "local_dir": "~/.ssh/p"},
                     {"project_name": "p", "local_dir": "~/torchbench/p"}, {"project_name": "p", "local_dir": "~/Desktop/../../x"},
                     {"project_name": "p", "remote_dir": "/scratch/other/p"}, {"project_name": "p", "remote_dir": "/scratch/abc1234/p; id"}):
            with self.assertRaises(config.ConfigError, msg=body):
                project.save(self.base, body)
        self.assertEqual(sorted(p.name for p in (self.home / "Desktop").iterdir()), [])
        self.assertEqual(config.load()[0]["project_name"], "")

    def test_an_existing_push_script_keeps_its_exclude_list(self):
        existing = self.home / "Desktop" / "wg"
        existing.mkdir()
        (existing / "push.sh").write_text("rsync -avz --cvs-exclude \\\n  --exclude=OLD-1 \\\n  --exclude=.git \\\n  --exclude='reports/generated' \\\n"
                                          "  --exclude='*.pdf' \\\n  --exclude results \\\n  \"$LOCAL_DIR/\" \"$REMOTE_DEST\"\n")
        saved = project.save(self.base, {"project_name": "wg"})
        self.assertTrue(saved["imported_excludes"])
        self.assertEqual(saved["config"]["push_excludes"], ["OLD-1", ".git", "reports/generated", "*.pdf", "results"])
        self.assertTrue(saved["config"]["push_cvs_exclude"])

    def test_status(self):
        cfg = project.save(self.base, {"project_name": "demo-project"})["config"]
        ssh = SSH("torch", CommandLog())
        st = project.status(cfg, ssh)
        self.assertEqual((st["configured"], st["local_exists"], st["local_empty"]), (True, True, True))
        self.assertEqual((st["connected"], st["remote_exists"]), (False, None))       # not signed in: unknown, not guessed
        self.open_login()
        self.assertTrue(project.status(cfg, ssh)["remote_exists"])
        other = project.save(cfg, {"project_name": "brand-new"})["config"]
        self.assertFalse(project.status(other, ssh)["remote_exists"])
        self.assertEqual(st["defaults"], {"local_parent": str(self.home / "Desktop"), "remote_parent": "/scratch/abc1234"})


@unittest.skipUnless(HAVE_RSYNC, "needs rsync")
class PushPull(FakeTorchCase):
    def setUp(self):
        super().setUp()
        self.mac = self.tmp / "machome" / "Desktop" / "study"
        (self.mac / "src").mkdir(parents=True)
        (self.mac / "src" / "train.py").write_text('print("v1")\n')
        (self.mac / "README.md").write_text("# study\n")
        (self.mac / "results").mkdir()
        (self.mac / "results" / "old.txt").write_text("a local result that must not go up\n")
        (self.mac / ".git").mkdir()
        (self.mac / ".git" / "HEAD").write_text("ref\n")
        self.torch = self.tmp / "fs" / "scratch" / "study"
        self.cfg = self.config(mode="owner", project_name="study", local_dir=str(self.mac), remote_dir="/scratch/abc1234/study")
        self.log = CommandLog()
        self.ssh = SSH("torch", self.log)
        self.sync = Sync(self.cfg, self.ssh, self.log)
        self.open_login()

    def finish(self, sync=None):
        sync = sync or self.sync
        self.wait_for(lambda: sync.job_state().get("done"), 20, what="the copy to finish")
        return sync.job_state()

    def push(self):
        plan = self.sync.preview("push")
        self.assertTrue(plan["ok"], plan)
        started = self.sync.start(plan["plan"])
        if started.get("started"):
            self.assertEqual(self.finish()["exit"], 0)
        return plan, started

    def test_preview_copies_nothing(self):
        plan = self.sync.preview("push")
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(sorted(plan["files"]), ["README.md", "src/train.py"])       # results and .git are left out
        self.assertEqual(plan["count"], 2)
        self.assertFalse(self.torch.exists())
        self.assertIn("-n", plan["commands"][0].split())
        self.assertIn("BatchMode=yes", plan["commands"][0])
        self.assertIn("ControlMaster=no", plan["commands"][0])

    def test_first_push_creates_the_folder_on_torch(self):
        plan, started = self.push()
        self.assertTrue(started["started"])
        self.assertEqual((self.torch / "src" / "train.py").read_text(), 'print("v1")\n')
        self.assertFalse((self.torch / "results").exists())
        self.assertFalse((self.torch / ".git").exists())
        again = self.sync.preview("push")
        self.assertEqual(again["count"], 0)                                           # nothing left to send
        self.assertTrue(self.sync.start(again["plan"])["nothing"])
        labels = [e["label"] for e in self.log.entries()]
        self.assertIn("Push code to Torch", labels)
        self.assertTrue(any(l.startswith("Preview: what a push would copy") for l in labels))

    def test_push_overwrites_edits_made_on_torch_and_deletes_nothing(self):
        self.push()
        time.sleep(1.1)
        (self.torch / "src" / "train.py").write_text('print("edited on torch")\n')
        (self.torch / "only_on_torch.txt").write_text("stays\n")
        (self.torch / "results").mkdir()
        (self.torch / "results" / "SUMMARY.md").write_text("accuracy 0.91\n")
        plan = self.sync.preview("push")
        self.assertEqual(plan["files"], ["src/train.py"])                             # the preview names what will be replaced
        self.assertTrue(self.sync.start(plan["plan"])["started"])
        self.finish()
        self.assertEqual((self.torch / "src" / "train.py").read_text(), 'print("v1")\n')
        self.assertEqual((self.torch / "only_on_torch.txt").read_text(), "stays\n")
        self.assertEqual((self.torch / "results" / "SUMMARY.md").read_text(), "accuracy 0.91\n")
        (self.mac / "README.md").unlink()                                             # deleting locally does not delete on Torch
        plan = self.sync.preview("push")
        self.assertEqual(plan["count"], 0)
        self.assertTrue((self.torch / "README.md").exists())

    def test_copy_is_refused_if_files_changed_after_the_preview(self):
        plan = self.sync.preview("push")
        (self.mac / "src" / "new_file.py").write_text("x = 1\n")
        result = self.sync.start(plan["plan"])
        self.assertTrue(result.get("changed"))                                         # shown a new preview instead
        self.assertEqual(sorted(result["files"]), ["README.md", "src/new_file.py", "src/train.py"])
        self.assertFalse(self.torch.exists())                                          # and nothing was copied
        self.assertTrue(self.sync.start(result["plan"])["started"])                    # confirming the new preview works
        self.finish()
        self.assertTrue((self.torch / "src" / "new_file.py").exists())

    def test_a_plan_cannot_be_invented_or_reused(self):
        self.assertTrue(self.sync.start("made-up")["stale"])
        plan = self.sync.preview("push")
        self.sync.start(plan["plan"])
        self.finish()
        self.assertTrue(self.sync.start(plan["plan"])["stale"])
        self.assertFalse(self.sync.preview("sideways")["ok"])

    def test_pull_brings_results_and_logs_and_leaves_weights(self):
        self.push()
        for rel, text in (("results/run1/SUMMARY.md", "accuracy 0.91\n"), ("results/run1/model.safetensors", "W" * 5000),
                          ("results/run1/checkpoints/step100.pt", "W" * 5000), ("logs/job_1.out", "done\n")):
            path = self.torch / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        (self.mac / "results" / "run1").mkdir()
        (self.mac / "results" / "run1" / "SUMMARY.md").write_text("stale local copy\n")
        plan = self.sync.preview("pull")
        self.assertTrue(plan["ok"], plan)
        self.assertEqual(sorted(plan["files"]), ["logs/job_1.out", "results/run1/SUMMARY.md"])
        self.assertEqual((self.mac / "results" / "run1" / "SUMMARY.md").read_text(), "stale local copy\n")   # preview copied nothing
        self.assertTrue(self.sync.start(plan["plan"])["started"])
        self.assertEqual(self.finish()["exit"], 0)
        self.assertEqual((self.mac / "results" / "run1" / "SUMMARY.md").read_text(), "accuracy 0.91\n")
        self.assertEqual((self.mac / "logs" / "job_1.out").read_text(), "done\n")
        self.assertFalse((self.mac / "results" / "run1" / "model.safetensors").exists())
        self.assertFalse((self.mac / "results" / "run1" / "checkpoints").exists())
        self.assertTrue((self.mac / "results" / "old.txt").exists())                   # nothing local is deleted
        self.assertTrue((self.torch / "results" / "run1" / "model.safetensors").exists())   # and nothing on Torch changes

    def test_pull_one_folder_and_folders_that_are_not_there_yet(self):
        self.push()
        plan = self.sync.preview("pull")                                               # no results or logs on Torch yet
        self.assertTrue(plan["ok"], plan)
        self.assertEqual((plan["count"], sorted(plan["not_there"])), (0, ["logs", "results"]))
        (self.torch / "results" / "a").mkdir(parents=True)
        (self.torch / "results" / "a" / "x.json").write_text("{}")
        (self.torch / "results" / "b").mkdir()
        (self.torch / "results" / "b" / "y.json").write_text("{}")
        plan = self.sync.preview("pull", "results/a")
        self.assertEqual(plan["files"], ["results/a/x.json"])
        self.sync.start(plan["plan"])
        self.finish()
        self.assertTrue((self.mac / "results" / "a" / "x.json").exists())
        self.assertFalse((self.mac / "results" / "b").exists())
        for bad in ("../outside", "results/a b", "results/$(id)", "/etc"):
            self.assertFalse(self.sync.preview("pull", bad)["ok"], bad)

    def test_colleague_can_pull_but_not_push(self):
        sync = Sync(dict(self.cfg, mode="colleague"), self.ssh, self.log)
        refused = sync.preview("push")
        self.assertTrue(refused["not_allowed"])
        self.assertFalse(self.torch.exists())
        self.assertTrue(sync.preview("pull")["ok"])

    def test_nothing_without_a_sign_in(self):
        self.close_login()
        self.assertTrue(self.sync.preview("push")["not_connected"])
        self.assertTrue(self.sync.preview("pull")["not_connected"])
        self.assertEqual([a for a in self.ssh_argvs() if "--server" in " ".join(a)], [])

    def test_sign_in_lost_between_preview_and_copy(self):
        plan = self.sync.preview("push")
        self.close_login()
        self.assertTrue(self.sync.start(plan["plan"])["not_connected"])
        self.assertFalse(self.torch.exists())

    def test_needs_both_folders(self):
        sync = Sync(dict(self.cfg, local_dir=""), self.ssh, self.log)
        self.assertTrue(sync.preview("push")["no_project"])
        sync = Sync(dict(self.cfg, local_dir=str(self.tmp / "machome" / "Desktop" / "gone")), self.ssh, self.log)
        self.assertIn("does not exist", sync.preview("push")["error"])


if __name__ == "__main__":
    unittest.main()
