"""The read-only file browser and log viewer, run through the real guard script (GNU tools on Linux)
and through the Python imitation the macOS demo uses."""
import os
import unittest

from helpers import FakeTorchCase
from torchbench import files
from torchbench.cmdlog import CommandLog
from torchbench.ssh import SSH


class Pure(unittest.TestCase):
    def test_log_problems_are_explained(self):
        text = ("step 10\nTraceback (most recent call last):\n  File x\n"
                "torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 2.50 GiB.\n"
                "OSError: [Errno 122] Disk quota exceeded\nModuleNotFoundError: No module named 'peft'\n"
                "slurmstepd: error: *** JOB 1 ON gl011 CANCELLED AT 2026-11-10T07:06:10 DUE TO TIME LIMIT ***\nall good\n")
        found = files.scan_log(text)
        self.assertEqual([f["line"] for f in found], [2, 4, 5, 6, 7])
        titles = [f["title"] for f in found]
        self.assertEqual(titles, ["The program stopped with a Python error", "The GPU ran out of memory", "Storage is full",
                                  "A Python package is missing", "The job ran out of time"])
        self.assertTrue(all(f["explanation"] for f in found))
        self.assertEqual(files.scan_log("step 1 loss 0.5\nstep 2 loss 0.4\n"), [])

    def test_kinds(self):
        for name, kind in (("SUMMARY.md", "markdown"), ("summary.json", "json"), ("m.csv", "csv"), ("loss.PNG", "image"),
                           ("job_1.err", "log"), ("train.py", "text"), ("metrics.jsonl", "text")):
            self.assertEqual(files.kind_of(name), kind, name)

    def test_follow_never_splits_a_character(self):
        whole = "naïve ✓".encode("utf-8")
        for cut in range(1, len(whole) + 1):
            trimmed = files._trim_partial_utf8(whole[:cut])
            trimmed.decode("utf-8")                      # must not raise
            self.assertLessEqual(len(whole[:cut]) - len(trimmed), 3)
        self.assertEqual(files._trim_partial_utf8(whole), whole)

    def test_listing_parser(self):
        out = files.parse_listing("f\t12\t1731370000.5\tf\tb.txt\nd\t4096\t1731370000.5\td\tz dir\n"
                                  "l\t9\t1731370000.5\td\tlink\nnot a line\nf\t3\t1731370000.5\tf\tname\twith tab\n")
        self.assertEqual([e["name"] for e in out["entries"]], ["z dir", "b.txt", "link", "name\twith tab"])
        self.assertEqual(out["skipped"], 1)
        self.assertEqual((out["entries"][2]["kind"], out["entries"][2]["link_to"]), ("link", "folder"))
        self.assertIsNone(out["entries"][0]["size"])


class Browser(FakeTorchCase):
    EMULATE = "0"

    def setUp(self):
        super().setUp()
        os.environ["FAKE_SSH_EMULATE"] = self.EMULATE
        self.log = CommandLog()
        self.files = files.Files(self.config(), SSH("torch", self.log))
        self.project = self.tmp / "project"
        self.open_login()

    def test_list_project_folder(self):
        top = self.files.list("")
        self.assertTrue(top["ok"], top)
        self.assertEqual([(e["name"], e["kind"]) for e in top["entries"]],
                         [("configs", "folder"), ("logs", "folder"), ("results", "folder"), ("README.md", "file")])
        sub = self.files.list("results/demo_v1/analyze")
        self.assertEqual(sorted(e["name"] for e in sub["entries"]), ["SUMMARY.md", "loss.png", "metrics.csv", "summary.json"])
        self.assertEqual({e["name"]: e["show_as"] for e in sub["entries"]}["SUMMARY.md"], "markdown")

    def test_read_summary_from_the_top(self):
        got = self.files.read("results/demo_v1/analyze/SUMMARY.md")
        self.assertTrue(got["ok"], got)
        self.assertTrue(got["text"].startswith("# Demo run: summary"))
        self.assertEqual((got["show_as"], got["start"], got["more_after"], got["binary"]), ("markdown", 0, False, False))
        self.assertEqual(got["end"], got["size"])

    def test_log_opens_at_the_end_and_follows(self):
        rel = "logs/finetune-batch_22601412_0.out"
        first = self.files.read(rel, start=-1)
        self.assertTrue(first["ok"], first)
        self.assertIn("step 1200/2000", first["text"])
        self.assertEqual(first["end"], first["size"])
        with open(self.project / rel, "a") as f:
            f.write("[2026-11-11 18:11:30] step 1600/2000 loss 0.502 ✓\n")
        more = self.files.read(rel, start=first["end"], following=True)
        self.assertEqual(more["text"], "[2026-11-11 18:11:30] step 1600/2000 loss 0.502 ✓\n")   # only what was added
        self.assertEqual(more["start"], first["end"])
        nothing = self.files.read(rel, start=more["end"], following=True)
        self.assertEqual((nothing["text"], nothing["end"]), ("", more["end"]))

    def test_long_log_opens_at_its_tail(self):
        big = self.project / "logs" / "big_1.out"
        with open(big, "w") as f:
            for i in range(60000):
                f.write("line %06d of a long log\n" % i)
        got = self.files.read("logs/big_1.out", start=-1)
        self.assertTrue(got["more_before"])
        self.assertLessEqual(len(got["text"]), 256 * 1024)
        self.assertTrue(got["text"].endswith("line 059999 of a long log\n"))
        top = self.files.read("logs/big_1.out")
        self.assertTrue(top["more_after"] and top["text"].startswith("line 000000"))

    def test_failed_job_log_is_annotated(self):
        got = self.files.read("logs/pilot-finetune_22590102_2.err", start=-1)
        titles = [p["title"] for p in got["problems"]]
        self.assertIn("The GPU ran out of memory", titles)
        self.assertIn("The program stopped with a Python error", titles)

    def test_job_logs_lookup(self):
        got = self.files.job_logs("22590102_2")
        self.assertTrue(got["ok"], got)
        names = [f["name"] for f in got["files"]]
        self.assertEqual(set(names[:2]), {"pilot-finetune_22590102_2.err", "pilot-finetune_22590102_2.out"})   # this task first
        self.assertIn("pilot-finetune_22590102_3.err", names)                                                   # then the batch
        self.assertEqual(got["files"][0]["rel"], "logs/" + names[0])
        self.assertEqual(self.files.job_logs("99999999")["files"], [])

    def test_download_is_byte_exact(self):
        got = self.files.download("results/demo_v1/analyze/loss.png")
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["data"], (self.project / "results/demo_v1/analyze/loss.png").read_bytes())
        self.assertEqual(got["content_type"], "image/png")

    def test_binary_file_is_not_shown_as_text(self):
        got = self.files.read("results/demo_v1/analyze/loss.png")
        self.assertTrue(got["binary"])
        self.assertEqual(got["text"], "")

    def test_cannot_leave_the_project_even_through_a_symlink(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("not yours to browse")
        os.symlink(outside, self.project / "escape")                 # a folder link pointing out
        os.symlink(outside / "secret.txt", self.project / "leak.txt")  # a file link pointing out
        os.symlink(self.project / "logs", self.project / "inside")   # a link that stays inside is fine
        for attempt in (lambda: self.files.list("escape"), lambda: self.files.read("leak.txt"),
                        lambda: self.files.read("escape/secret.txt"), lambda: self.files.download("leak.txt")):
            got = attempt()
            self.assertFalse(got["ok"])
            self.assertIn("outside the project folder", got["error"])
        self.assertTrue(self.files.list("inside")["ok"])
        listed = {e["name"]: e for e in self.files.list("")["entries"]}
        self.assertEqual((listed["escape"]["kind"], listed["escape"]["link_to"]), ("link", "folder"))
        for text in ("../outside/secret.txt", "/etc/passwd"):
            with self.assertRaises(Exception):
                self.files.read(text)

    def test_missing_and_wrong_kind(self):
        self.assertIn("No such file", self.files.read("nope.txt")["error"])
        self.assertIn("not a folder", self.files.list("README.md")["error"])
        self.assertIn("not a file", self.files.read("logs")["error"])

    def test_file_contents_stay_out_of_the_log(self):
        self.files.read("results/demo_v1/analyze/SUMMARY.md")
        self.files.download("results/demo_v1/analyze/loss.png")
        entries = self.log.entries()
        self.assertTrue(any("Read results/demo_v1/analyze/SUMMARY.md" == e["label"] for e in entries))
        joined = " ".join(e["stdout"] for e in entries)
        self.assertNotIn("Demo run: summary", joined)
        self.assertIn("bytes of file contents", joined)
        self.assertTrue(all(e["command"].startswith(("bash -c", "ssh -O")) for e in entries))   # the command itself is logged in full

    def test_nothing_is_written_on_the_cluster(self):
        before = sorted((str(p), p.stat().st_mtime, p.stat().st_size) for p in self.project.rglob("*"))
        self.files.list("")
        self.files.list("logs")
        self.files.read("README.md")
        self.files.read("logs/calibrate_22601411.out", start=-1)
        self.files.job_logs("22601411")
        self.files.download("README.md")
        after = sorted((str(p), p.stat().st_mtime, p.stat().st_size) for p in self.project.rglob("*"))
        self.assertEqual(before, after)

    def test_not_connected(self):
        self.close_login()
        got = self.files.list("")
        self.assertTrue(got["not_connected"])
        self.assertEqual(self.remote_commands(), [])


class BrowserImitation(Browser):
    """The same behaviour from the Python imitation (what `torchbench demo` uses on macOS)."""
    EMULATE = "1"


if __name__ == "__main__":
    unittest.main()
