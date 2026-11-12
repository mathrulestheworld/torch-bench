"""Run every parser over real recordings, when there are any.

Record them with `torchbench record-fixtures` (signed in), review, and copy the folder to
tests/fixtures/real/<date>/. Until then this test is skipped and the parsers are only checked
against the synthetic demo fixtures.
"""
import unittest
from pathlib import Path

import helpers  # noqa: F401  (sets up the import path)
from torchbench import slurm

REAL = Path(__file__).resolve().parent / "fixtures" / "real"
SETS = sorted(p for p in REAL.iterdir() if p.is_dir()) if REAL.is_dir() else []


@unittest.skipUnless(SETS, "no real recordings in tests/fixtures/real/ yet")
class RealRecordings(unittest.TestCase):
    def each(self, name):
        for folder in SETS:
            path = folder / name
            if path.exists():
                yield folder.name, path.read_text(encoding="utf-8")

    def test_squeue(self):
        for label, text in self.each("squeue.txt"):
            parsed = slurm.parse_squeue(text)
            self.assertEqual(parsed["skipped"], 0, label)
            for job in parsed["jobs"]:
                self.assertNotEqual(job["kind"], "other", "%s: unknown state %s" % (label, job["state"]))

    def test_sacct(self):
        for label, text in self.each("sacct.txt"):
            parsed = slurm.parse_sacct(text)
            self.assertEqual(parsed["skipped"], 0, label)
            self.assertTrue(all(j["partition"] for j in parsed["jobs"]), label)

    def test_sshare(self):
        for label, text in self.each("sshare.txt"):
            parsed = slurm.parse_sshare(text)
            self.assertTrue(parsed["rows"], label)
            self.assertEqual(parsed["skipped"], 0, label)

    def test_sprio(self):
        for label, text in self.each("sprio.txt"):
            self.assertEqual(slurm.parse_sprio(text)["skipped"], 0, label)

    def test_sinfo(self):
        for label, text in self.each("sinfo.txt"):
            parsed = slurm.parse_sinfo(text)
            self.assertEqual(parsed["skipped"], 0, label)
            self.assertTrue(any(p["gpus_total"] for p in parsed["partitions"].values()), label)

    def test_myquota(self):
        for label, text in self.each("myquota.txt"):
            parsed = slurm.parse_myquota(text)
            self.assertTrue(parsed["parsed"], "%s: myquota format not recognised" % label)
            self.assertIn("/scratch", [f["fs"] for f in parsed["filesystems"]], label)


if __name__ == "__main__":
    unittest.main()
