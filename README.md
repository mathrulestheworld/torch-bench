# Torch Bench

A small Mac app for using NYU's Torch HPC cluster without the command line. It runs on your Mac, opens in
your browser, and talks to Torch over one shared SSH sign-in. Nothing is installed on Torch.

**Status: M0 and M1 of the build brief (connection, dashboard, files, logs, terminal, push and pull).** The
app looks at Torch, and copies files in two directions after showing you exactly what it would copy. It
cannot submit or cancel jobs or delete anything. The terminal is the one deliberate exception, where you type
as yourself. It has been tested offline against a stand-in for ssh and made-up data; see "Before you rely on
it" for what still needs a real run.

## What it does

- **Guided setup**: one page with the NYU links a newcomer needs (account, project, VPN, Duo, docs), then
  NetID, the SSH settings entry, and sign-in.
- **Guided sign-in**: Torch Bench starts `ssh torch` itself, opens Microsoft's device-login page, shows the
  one-time code (already copied), and waits. You sign in and approve Duo yourself. If the login host is
  unreachable you get a "Connect to the NYU VPN first" page. Anything it does not recognise is shown exactly
  as Torch printed it, with a reply box. A plain `ssh torch` in Terminal also works and is detected.
- **Jobs**: what is running and waiting, in a sentence; each waiting job with a plain-language reason
  ("You've used a lot of GPU hours recently, so other jobs go first"), the scheduler's start estimate when it
  gives one, and Torch Bench's own estimate from how long your recent jobs really waited. Fair-share score,
  storage quota with a loud warning above 90%, free GPUs per partition, and the last 7 days of finished
  jobs. Each job has "Find this job's log files".
- **Project**: a project is a folder of code on your Mac and its copy on Torch, and you choose both. Typing
  a name fills in `~/Desktop/<name>` and `/scratch/<netid>/<name>`; change either, or pick the Mac folder
  with the macOS folder chooser. The Mac folder is created if missing; the first push creates the Torch one.
  **Push** sends code Mac → Torch (replaces files that differ, deletes nothing, leaves out results, logs,
  `.git` and the like). **Pull** brings `results` and `logs` back (replaces local copies that differ,
  deletes nothing, leaves model weights on Torch). Both show the exact file list first and copy only when
  you confirm it; if anything changed in between, you get the new list instead. In Files, any folder of the
  project has "Copy this folder to my Mac".
- **Files**: a read-only browser of your own folders on Torch: home, scratch, archive, and the project
  folder when one is set. Markdown, JSON, CSV and pictures are shown in place (a results `SUMMARY.md` reads
  like a page); anything can be downloaded (up to 100 MB). Logs open at the end, can follow new lines, and
  common failures (GPU out of memory, "Disk quota exceeded", a missing Python package, time limit, out of
  RAM) are highlighted with a one-line explanation.
- **Terminal**: a real shell on the Torch login node, inside the window, over the shared sign-in. It opens
  in the project folder. A notice reminds that it is the login node (no heavy work). What appears on screen
  is recorded in `~/torchbench/logs/terminal/` and listed in the activity log.
- **Help**: the NYU links (kept in the settings file so they can be changed), Torch's house rules, and the
  **activity log**: every command sent to Torch with its output, also saved under
  `~/torchbench/logs/torch_shell/` (`history.log`, `last.txt`, `commands.jsonl`).

Planned next, in order: M2 launchers with a dry run, guarded cancel, notifications; M3 disk manager,
GPU-utilisation check, "copy for Claude".

## Install

Open Terminal on your Mac and paste this one line:

    curl -fsSL https://raw.githubusercontent.com/mathrulestheworld/torch-bench/main/install.sh | bash

The installer:

- checks for Apple's Command Line Tools (they include Python) and offers to install them if missing;
- puts Torch Bench in Applications and opens it;
- adds a `torchbench` command for Terminal;
- needs no sudo and no pip, and installs nothing on Torch.

Run the same line again to update; your settings are kept. The app is built on your Mac rather than
downloaded as an app, so macOS shows no security warning.

    torchbench            start it and open the window
    torchbench stop       stop it (your Torch sign-in stays open)
    torchbench status     is it running, and is a Torch sign-in open?
    torchbench demo       made-up data; touches neither Torch nor your settings
    torchbench record-fixtures [folder]    save Torch's real answers for the tests

To look around before you have a Torch account, run `torchbench demo`.

To uninstall: `torchbench stop`, delete `~/torchbench` and Torch Bench in Applications, remove the
`# torchbench` line from `~/.zprofile`, and remove the block between `# >>> torchbench >>>` and
`# <<< torchbench <<<` in `~/.ssh/config`.

For development, clone the repo and run `./torchbench` (or `bash install.sh`) from it.

## Before you rely on it (owner checklist for M0)

These need a Mac and a real Torch sign-in, which the build environment did not have.

1. **Run it on a Mac**: paste the install line, go through setup with your own NetID.
2. **Do one real sign-in through the app.** Step detection follows the prompt NYU documents
   ("Authenticate with PIN ... at https://microsoft.com/devicelogin and press ENTER"). If Torch prints
   something else, the raw-output fallback still gets you in. Either way, afterwards go to Help and choose
   "Save what Torch printed at the last sign-in" (codes and typed text are blanked), so the patterns in
   `app/torchbench/login.py` can be confirmed and a real transcript added to `tests/test_login.py`.
3. **Record real command output**: signed in, run `torchbench record-fixtures`, look the files over (they
   hold job names and your NetID), copy the folder to `tests/fixtures/real/<date>/`, and run the tests.
   `tests/test_real_fixtures.py` runs every parser over them. `myquota` is the likeliest to differ: its
   layout here is a guess from NYU's earlier cluster. If it differs, the window shows the raw text rather
   than a wrong number.
4. **Kill the shared login** (`ssh -O exit torch`) with the window open: the "Not connected" notice should
   appear within one refresh, with no hang and no prompt.
5. **Files and Terminal on the real cluster**: open Home, Scratch and Archive, a folder, a `SUMMARY.md`, a job's `.err`, tick "Follow new
   lines" on a running job's log, download a file, and run a few commands in the Terminal (including a
   full-screen one such as `less` or `nano`, and resizing the window). The file commands need GNU `find`,
   `stat` and `realpath` on Torch, which Red Hat provides.
6. **Project, push and pull**: set up the project with its real folders, press Push and read the preview
   before confirming (it should list only files you changed since the last push), then Pull. The folder
   chooser button and macOS's own rsync are untested until this run.
7. Check the **help links** in `~/torchbench/config.json` (the VPN one points at a Stern IT page).

## Safety rules, and where each is encoded

| Rule | Where |
| --- | --- |
| No password or code is ever stored or logged. The app starts ssh and shows its prompts; the person authenticates. | `login.py` (typed text goes straight to the pty), `redact.py`, `tests/test_login.py::test_password_prompt_is_piped_and_never_kept` |
| Read-only by default. The app's only operations that change files are push and pull. | `commands.py` is the only place commands are built, all read-only; `ssh.py` refuses anything else and anything marked mutating; `policy.py` |
| Push and pull copy only what you confirmed, and never delete. | `sync.py` is the only place an rsync command is built (no `--delete`). `preview()` is a dry run; `start()` repeats it and copies only if the file list is unchanged. Locations come from the settings and are validated: the Torch folder must be inside your own scratch, home or archive; the Mac folder inside your home folder. Pushing is for the `owner` role. `tests/test_project_sync.py` |
| The file browser stays inside your own folders and cannot change anything. | It starts from a fixed list of places (`config.places`: project, home, scratch, archive, or `browse_roots` in the settings). Paths are relative and validated in `commands.py`; one fixed guard script resolves the real path on Torch and refuses anything outside the chosen place, symlinks included; the scripts only call `realpath`, `find`, `stat`, `tail`, `head`, `cat`. `tests/test_files.py` |
| The terminal is the one escape hatch; the app never types into it; sessions are recorded with secrets blanked. | `terminal.py` (only screen output is recorded, never keystrokes; token-shaped text and `NAME=secret` are blanked), `tests/test_terminal.py` |
| Nothing heavy on the login node. | The registry holds only `squeue`, `sacct`, `sshare`, `sprio`, `sinfo`, `myquota`, `scontrol show job`, `du -sh` (manual button only), `hostname`, and the file reads above. Refreshes are stale-driven and stop when the window is hidden. |
| Nothing is deleted on the cluster. | No such command exists in the registry. |
| Every remote command and its output is logged and visible. | `cmdlog.py`, the activity log under Help. For file reads the command is logged in full and the contents as a byte count, so viewing a file does not copy it into the log. |
| If `ssh -O check` fails, no command runs. | `ssh.py`; `tests/test_safety.py::test_no_login_means_nothing_runs` |
| Commands can never prompt or hang. | `BatchMode=yes`, `ControlMaster=no`, closed stdin, connect and hard timeouts in `ssh.py` |
| Only this Mac can talk to the app. | `server.py`: binds 127.0.0.1, per-launch page token, Host check, strict Content-Security-Policy |

Two deliberate choices to know about:

- The SSH entry uses NYU's recommended `StrictHostKeyChecking no` / `UserKnownHostsFile /dev/null`, because
  `login.torch.hpc.nyu.edu` points at several machines with different host keys. That trades host-key
  checking for not being blocked by key-mismatch errors. If you would rather keep checking, edit the block.
- If you already have your own working `Host torch` entry, Torch Bench uses it and writes nothing.
- Push and pull are the app's own rsync, driven by the settings, rather than running the project's `push.sh`:
  that is what makes the preview and the chosen locations possible. If the Mac folder has a `push.sh`, its
  `--exclude` list (and `--cvs-exclude`) is copied into the settings when the project is set up, so the same
  files are left out. Anything else that script does is not run.
- Pull leaves out `*.safetensors`, `*.pt`, `*.pth`, `*.bin`, `*.ckpt`, `cache/` and `checkpoints/` by default.
  If a project keeps small analysis results in `.pt` files, edit `pull_excludes` in the settings.
- Downloads go through `ssh ... cat` rather than `scp`, so the same folder guard applies to them.
- The brief scoped the file browser to the project folder; at the owner's request it now also covers the
  person's own home, scratch and archive folders.
- The terminal is a real shell on your own account: it can do anything you can. In `colleague` mode it is
  available too, because it acts only as the person typing.

## Settings (`~/torchbench/config.json`)

Created by setup. Everything user- or project-specific lives here; nothing from any research project is
hard-coded in the program.

| Key | Meaning |
| --- | --- |
| `netid` | Your NYU NetID |
| `mode` | `colleague` (default: watch and read) or `owner` (later: also submit, cancel, clean up) |
| `ssh_alias`, `remote_host` | `torch`, `login.torch.hpc.nyu.edu` |
| `project_name`, `remote_dir`, `local_dir` | The project, its folder on Torch (default `/scratch/<netid>/<name>`), its folder on this Mac (default `~/Desktop/<name>`). Set on the Project page |
| `sbatch_account` | The SLURM account jobs are charged to |
| `default_partition`, `gpu_partitions`, `cpu_partitions` | `l40s_public`; `l40s_public`, `h200_public`; `cs` |
| `launchers` | Registered submit scripts (used from M2) |
| `help_links` | The list on the Help page: `title`, `url`, `note` |
| `quota_warn_percent`, `history_days` | 90, 7 |
| `push_excludes`, `push_cvs_exclude` | What a push leaves out (rsync patterns) |
| `pull_paths`, `pull_excludes` | Folders a pull brings back (`results`, `logs`) and what it leaves on Torch (weights) |
| `browse_roots` | Folders the Files page offers, as `label` + `path`. Empty means `/home/<netid>`, `/scratch/<netid>`, `/archive/<netid>` |

An owner can save a **project file** from the Help page; a colleague loads it during setup and only has to
type their NetID.

## Layout

    install.sh, torchbench        installer and command
    app/torchbench/               the program (Python 3.9+, standard library only)
      commands.py                 the registry: the only commands that can reach Torch
      ssh.py                      ControlMaster + BatchMode wrapper (the scripts/torch.sh pattern)
      login.py, ptyexec.py        guided sign-in under a pty
      project.py, sync.py         the project's two folders; push and pull (preview, then confirmed copy)
      files.py                    read-only file browser, log viewer, failure explanations
      terminal.py                 the built-in terminal (pty, recording)
      sshconf.py                  the marked block in ~/.ssh/config
      slurm.py, reasons.py, waits.py, dashboard.py    parsing, plain-language reasons, wait estimates
      cmdlog.py, redact.py        transparency log, secret blanking
      config.py, policy.py, paths.py, server.py, __main__.py
    app/static/                   the window (one page; vendor/ holds xterm.js, MIT, for the terminal)
    tests/                        unittest suite; fake_ssh.py stands in for ssh; fixtures/demo is made up

## Tests

    python3 -m unittest discover -s tests

158 tests (6 of them wait for real recordings), all offline: the SSH wrapper, the parsers, the registry,
redaction, the SSH-settings writer, the guided sign-in and the terminal end to end under a real pty, the file
browser (through the real guard script and through the imitation the macOS demo uses), project setup, push
and pull with a real rsync at both ends, the dashboard, and the server. They pass on Python 3.9 (what macOS ships) and 3.13.

Acceptance tests from the build brief covered so far, and how:

| Acceptance test | State |
| --- | --- |
| Fresh Mac, no ssh config: installer + wizard reaches a connected dashboard with one guided sign-in | Passes against the stand-in (`test_server.py::test_fresh_mac_to_green_dashboard`); needs the real run above |
| Kill the ControlMaster: "Not connected" within one refresh, no hangs, no prompts | Passes against the stand-in (`test_dashboard.py::test_losing_the_login_degrades_within_one_refresh`, `test_safety.py`) |
| Pending array job shows a human-readable reason and the fair-share number | Passes (`test_dashboard.py`) |
| Quota above 90% warns loudly; the submit block is ready for M2 | Passes (`test_dashboard.py`, `test_safety.py::test_full_scratch_blocks_submit`) |
| Tail a running job's `.err` live; download a `SUMMARY.md` and see it rendered | Passes against the stand-in (`test_files.py`, and walked in a browser in demo mode); needs the real run above |
| Push and pull show their output and say plainly that push overwrites the cluster copy | Passes against the stand-in (`test_project_sync.py`, walked in a browser in demo mode); needs the real run above |
| Unit tests for the SSH wrapper, parsers and registry against fixtures | Passes, on synthetic fixtures; real ones to be recorded |

The brief's wording "only one Terminal login" was changed to "one guided sign-in" and rule 1's "never
automate the login" to "never store or enter credentials for the person", as agreed with the owner.
