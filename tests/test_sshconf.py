import os
import unittest

from helpers import FakeTorchCase
from torchbench import sshconf


class SshConfig(FakeTorchCase):
    def path(self):
        return self.tmp / "ssh" / "config"

    def test_fresh_mac_gets_the_block(self):
        cfg = self.config()
        st = sshconf.status(cfg)
        self.assertFalse(st["ok"])
        self.assertTrue(st["can_write"])
        st = sshconf.install(cfg)
        self.assertTrue(st["ok"], st["problems"])
        text = self.path().read_text()
        for line in ("Host torch", "HostName login.torch.hpc.nyu.edu", "User abc1234", "ControlMaster auto",
                     "ControlPath ~/.ssh/torchbench-%C", "ControlPersist 12h"):
            self.assertIn(line, text)
        self.assertEqual(oct(self.path().stat().st_mode & 0o777), "0o600")
        self.assertNotIn("assword", text)

    def test_existing_lines_are_kept_and_backed_up(self):
        self.path().parent.mkdir(parents=True)
        self.path().write_text("Host github.com\n  User git\n  IdentityFile ~/.ssh/id_ed25519")
        sshconf.install(self.config())
        text = self.path().read_text()
        self.assertTrue(text.startswith("Host github.com\n  User git\n  IdentityFile ~/.ssh/id_ed25519\n"))
        self.assertIn("Host torch", text)
        backups = [p for p in self.path().parent.iterdir() if "torchbench-backup" in p.name]
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), "Host github.com\n  User git\n  IdentityFile ~/.ssh/id_ed25519")

    def test_installing_twice_changes_nothing(self):
        sshconf.install(self.config())
        once = self.path().read_text()
        sshconf.install(self.config())
        self.assertEqual(self.path().read_text(), once)
        self.assertEqual(once.count("Host torch"), 1)

    def test_netid_change_rewrites_only_the_block(self):
        self.path().parent.mkdir(parents=True)
        self.path().write_text("Host other\n  User me\n")
        sshconf.install(self.config())
        sshconf.install(self.config(netid="xyz9876"))
        text = self.path().read_text()
        self.assertIn("User xyz9876", text)
        self.assertNotIn("User abc1234", text)
        self.assertIn("Host other\n  User me\n", text)
        self.assertEqual(text.count("Host torch"), 1)

    def test_owners_working_entry_is_left_alone(self):
        self.path().parent.mkdir(parents=True)
        own = ("Host torch\n  HostName login.torch.hpc.nyu.edu\n  User abc1234\n  ControlMaster auto\n"
               "  ControlPath ~/.ssh/cm-%r@%h:%p\n  ControlPersist 8h\n")
        self.path().write_text(own)
        st = sshconf.install(self.config())
        self.assertTrue(st["ok"])
        self.assertFalse(st["has_block"])
        self.assertEqual(self.path().read_text(), own)

    def test_own_entry_without_sharing_is_not_edited(self):
        self.path().parent.mkdir(parents=True)
        own = "Host torch\n  HostName login.torch.hpc.nyu.edu\n  User abc1234\n"
        self.path().write_text(own)
        st = sshconf.status(self.config())
        self.assertFalse(st["ok"])
        self.assertTrue(st["own_entry"])
        self.assertFalse(st["can_write"])
        with self.assertRaises(RuntimeError):
            sshconf.install(self.config())
        self.assertEqual(self.path().read_text(), own)


if __name__ == "__main__":
    unittest.main()
