"""The safety rules, as tests: the registry, the SSH wrapper, redaction and the config validator."""
import os
import unittest

from helpers import FakeTorchCase
from torchbench import commands, config, policy
from torchbench.cmdlog import CommandLog
from torchbench.redact import Redactor
from torchbench.ssh import SSH


class Registry(unittest.TestCase):
    def test_everything_registered_is_read_only(self):
        built = [commands.probe(), commands.queue(), commands.history("2026-11-04"), commands.fairshare("abc1234"),
                 commands.priority("abc1234"), commands.partitions(["l40s_public", "cs"]), commands.quota(),
                 commands.job_detail("22601412_3"), commands.disk_usage("/scratch/abc1234/demo-project")]
        self.assertEqual(sorted(c.name for c in built), sorted(commands.all_names()))
        for c in built:
            self.assertTrue(c.from_registry())
            self.assertFalse(c.mutating, c.name)
            first = c.text.split()[0]
            self.assertIn(first, ("hostname", "squeue", "sacct", "sshare", "sprio", "sinfo", "myquota", "scontrol", "du"))
            for word in ("sbatch", "scancel", "rm ", "srun", "python", "apptainer", ">", "|", ";", "&&", "$("):
                if word == "|" and first in ("squeue", "sprio"):
                    continue          # the '|' there is inside a quoted format string
                self.assertNotIn(word, c.text, c.name)

    def test_exact_command_text(self):
        self.assertEqual(commands.queue().text, "squeue --me -h -o '%i|%T|%M|%l|%P|%r|%S|%V|%D|%b|%N|%Q|%j'")
        self.assertEqual(commands.job_detail("123_4").text, "scontrol show job 123_4")
        self.assertTrue(commands.history("2026-11-04").text.startswith("sacct -X -P -S 2026-11-04 --format=JobID,State,"))

    def test_bad_arguments_build_nothing(self):
        for bad in ("1; rm -rf ~", "12 34", "$(id)", "", "abc", "1_2_3"):
            with self.assertRaises(commands.CommandRefused):
                commands.job_detail(bad)
        for bad in ("abc1234; id", "ABC", "a b", "-u root"):
            with self.assertRaises(commands.CommandRefused):
                commands.fairshare(bad)
        for bad in ("/scratch/abc; rm -rf x", "relative/path", "/scratch/../etc", "/scratch/a b", "/scratch/$(id)"):
            with self.assertRaises(commands.CommandRefused):
                commands.disk_usage(bad)
        with self.assertRaises(commands.CommandRefused):
            commands.partitions(["l40s_public", "cs;id"])
        with self.assertRaises(commands.CommandRefused):
            commands.history("yesterday; id")


class Wrapper(FakeTorchCase):
    def setUp(self):
        super().setUp()
        self.log = CommandLog()
        self.ssh = SSH("torch", self.log)

    def test_no_login_means_nothing_runs(self):
        result = self.ssh.run(commands.queue())
        self.assertTrue(result.not_connected)
        self.assertIsNone(result.exit_code)
        self.assertEqual(self.remote_commands(), [])
        # only `ssh -O check` was ever invoked
        self.assertEqual([a[:2] for a in self.ssh_argvs()], [["-O", "check"]])
        history = (self.tmp / "torchbench" / "logs" / "torch_shell" / "history.log").read_text()
        self.assertIn("[no login]  squeue --me", history)

    def test_runs_over_the_shared_login_in_batch_mode(self):
        self.open_login()
        result = self.ssh.run(commands.queue())
        self.assertTrue(result.ok)
        self.assertIn("finetune-batch", result.stdout)
        self.assertEqual(self.remote_commands(), [commands.queue().text])
        check, run = self.ssh_argvs()
        self.assertEqual(check, ["-O", "check", "torch"])
        self.assertEqual(run, ["-o", "BatchMode=yes", "-o", "ControlMaster=no", "-o", "ConnectTimeout=10", "torch",
                               commands.queue().text])
        shell = self.tmp / "torchbench" / "logs" / "torch_shell"
        self.assertIn("[exit 0]  squeue --me", (shell / "history.log").read_text())
        self.assertIn("finetune-batch", (shell / "last.txt").read_text())
        self.assertIn("finetune-batch", (shell / "commands.jsonl").read_text())

    def test_only_registry_commands_run(self):
        self.open_login()
        with self.assertRaises(PermissionError):
            self.ssh.run("squeue --me")                                   # a bare string
        forged = commands.RemoteCommand(name="x", text="scancel 1", label="", routine=False, mutating=False, timeout=5)
        with self.assertRaises(PermissionError):
            self.ssh.run(forged)                                          # not built by the registry
        self.assertEqual(self.remote_commands(), [])

    def test_mutating_commands_are_refused(self):
        self.open_login()
        import dataclasses
        mutating = dataclasses.replace(commands.queue(), mutating=True)
        with self.assertRaises(PermissionError):
            self.ssh.run(mutating)
        self.assertEqual(self.remote_commands(), [])

    def test_slow_cluster_never_hangs(self):
        self.open_login()
        os.environ["FAKE_SSH_SLEEP"] = "5"
        import dataclasses, time
        quick = dataclasses.replace(commands.queue(), timeout=1)
        start = time.time()
        result = self.ssh.run(quick)
        self.assertLess(time.time() - start, 4)
        self.assertTrue(result.timed_out)
        self.assertFalse(result.ok)

    def test_failed_command_is_reported(self):
        self.open_login()
        os.environ["FAKE_SSH_FAIL"] = "sacct"
        result = self.ssh.run(commands.history("2026-11-04"))
        self.assertEqual(result.exit_code, 1)
        self.assertIn("simulated failure", result.problem())

    def test_close_login(self):
        self.open_login()
        self.assertTrue(self.ssh.check())
        self.assertTrue(self.ssh.close_login())
        self.assertFalse(self.ssh.check())


class Redaction(unittest.TestCase):
    def test_typed_text_and_codes_are_blanked(self):
        r = Redactor()
        r.remember("correct horse battery")
        text = r.clean("Password: \nyou typed correct horse battery\nAuthenticate with PIN BUUM5YHJX at https://microsoft.com/devicelogin")
        self.assertNotIn("correct horse", text)
        self.assertNotIn("BUUM5YHJX", text)
        self.assertIn("https://microsoft.com/devicelogin", text)

    def test_ordinary_output_survives(self):
        r = Redactor()
        r.remember("1")          # too short to blank
        text = "22601412_1|RUNNING|exit code changes nothing|ExitCode 0:0"
        self.assertEqual(r.clean(text), text)

    def test_log_never_holds_a_remembered_secret(self):
        r = Redactor()
        log = CommandLog(r, persist=False)
        r.remember("hunter2hunter2")
        entry = log.record("echo hunter2hunter2", 0, "hunter2hunter2\n", "warn hunter2hunter2")
        self.assertNotIn("hunter2", entry["command"] + entry["stdout"] + entry["stderr"])


class Config(FakeTorchCase):
    def test_round_trip(self):
        cfg = config.save(self.config())
        loaded, problem = config.load()
        self.assertEqual(problem, "")
        self.assertEqual(loaded, cfg)
        self.assertEqual(oct((self.tmp / "torchbench" / "config.json").stat().st_mode & 0o777), "0o600")

    def test_default_role_is_colleague(self):
        self.assertEqual(config.defaults()["mode"], "colleague")

    def test_rejects_values_that_could_reach_a_shell(self):
        for key, bad in (("netid", "abc; id"), ("netid", ""), ("remote_dir", "/scratch/x; rm -rf /"),
                         ("remote_dir", "/scratch/../../etc"), ("sbatch_account", "a b"), ("ssh_alias", "torch -oProxyCommand=x"),
                         ("remote_host", "host;id"), ("mode", "admin"), ("gpu_partitions", ["ok", "bad name"]),
                         ("help_links", [{"title": "x", "url": "javascript:alert(1)"}])):
            with self.assertRaises(config.ConfigError, msg="%s=%r" % (key, bad)):
                self.config(**{key: bad})

    def test_project_file_has_no_identity(self):
        shared = config.shareable(self.config(mode="owner"))
        self.assertNotIn("netid", shared)
        self.assertNotIn("mode", shared)
        self.assertEqual(shared["remote_dir_template"], "/scratch/{netid}/demo-project")

    def test_broken_file_is_reported_not_guessed(self):
        (self.tmp / "torchbench").mkdir(parents=True)
        (self.tmp / "torchbench" / "config.json").write_text("{not json")
        cfg, problem = config.load()
        self.assertEqual(cfg, {})
        self.assertIn("Could not read", problem)


class Policy(unittest.TestCase):
    def test_this_version_only_views(self):
        for mode in ("owner", "colleague"):
            self.assertTrue(policy.allowed(mode, "view"))
            for action in ("push", "pull", "submit", "cancel", "cleanup"):
                self.assertFalse(policy.allowed(mode, action), action)

    def test_full_scratch_blocks_submit(self):
        from helpers import fixture
        from torchbench import slurm
        quota = slurm.parse_myquota(fixture("myquota.txt"))          # /scratch at 92%
        problems = policy.quota_blocks_submit(quota, 90)
        self.assertEqual(len(problems), 1)
        self.assertIn("/scratch is 92% full", problems[0])
        self.assertEqual(policy.quota_blocks_submit(quota, 95), [])
        self.assertTrue(policy.quota_blocks_submit(None, 90))         # unknown quota blocks too
        self.assertTrue(policy.quota_blocks_submit(slurm.parse_myquota("garbage"), 90))


if __name__ == "__main__":
    unittest.main()
