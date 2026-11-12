"""Shared test setup: a throwaway home folder and the stand-in ssh. Nothing here touches the network."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "tests"))

FAKE_SSH = str(ROOT / "tests" / "fake_ssh.py")
DEMO = ROOT / "tests" / "fixtures" / "demo"

import fake_ssh  # noqa: E402


def fixture(name, at=None):
    """A demo fixture with its time tokens filled in."""
    return fake_ssh.render((DEMO / name).read_text(encoding="utf-8"), at)


class FakeTorchCase(unittest.TestCase):
    """Each test gets its own ~/torchbench, ssh config and fake cluster state."""

    ENV = ("TORCHBENCH_HOME", "TORCHBENCH_SSH", "TORCHBENCH_SSH_CONFIG", "FAKE_SSH_STATE", "FAKE_SSH_FIXTURES",
           "FAKE_SSH_LOGIN", "FAKE_SSH_SLEEP", "FAKE_SSH_FAIL", "FAKE_SSH_ARGV_LOG", "TORCHBENCH_DEMO", "FAKE_SSH_NOW")

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="torchbench-test-"))
        self._saved = {k: os.environ.get(k) for k in self.ENV}
        for k in self.ENV:
            os.environ.pop(k, None)
        os.environ.update({
            "TORCHBENCH_HOME": str(self.tmp / "torchbench"),
            "TORCHBENCH_SSH": FAKE_SSH,
            "TORCHBENCH_SSH_CONFIG": str(self.tmp / "ssh" / "config"),
            "FAKE_SSH_STATE": str(self.tmp / "state"),
            "FAKE_SSH_FIXTURES": str(DEMO),
            "FAKE_SSH_ARGV_LOG": str(self.tmp / "argv.log"),
        })

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- conveniences ---------------------------------------------------------------------------
    def config(self, **over):
        from torchbench import config
        raw = config.defaults()
        raw.update({"netid": "abc1234", "project_name": "demo-project", "remote_dir": "/scratch/abc1234/demo-project",
                    "sbatch_account": "torch_pr_000_general"})
        raw.update(over)
        return config.validate(raw)

    def open_login(self):
        (self.tmp / "state").mkdir(parents=True, exist_ok=True)
        (self.tmp / "state" / "master").write_text("1")

    def close_login(self):
        try:
            (self.tmp / "state" / "master").unlink()
        except FileNotFoundError:
            pass

    def remote_commands(self):
        """Every command the fake cluster was actually asked to run."""
        path = self.tmp / "state" / "commands.log"
        return path.read_text().splitlines() if path.exists() else []

    def ssh_argvs(self):
        path = self.tmp / "argv.log"
        return [line.split("\x1f") for line in path.read_text().splitlines()] if path.exists() else []

    def wait_for(self, predicate, seconds=8.0, what="condition"):
        end = time.time() + seconds
        while time.time() < end:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail("timed out waiting for %s" % what)
