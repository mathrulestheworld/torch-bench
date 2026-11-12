"""Helper for the guided sign-in: make a terminal the controlling terminal, then become ssh.

ssh asks its sign-in questions on the controlling terminal, so it needs one. Running this tiny
single-threaded helper as its own process (instead of forking inside the threaded server) is the
safe way to give it one.

    python3 ptyexec.py <tty path> <program> [args...]
"""
import fcntl
import os
import sys
import termios


def main() -> None:
    tty, argv = sys.argv[1], sys.argv[2:]
    fd = os.open(tty, os.O_RDWR)
    try:
        fcntl.ioctl(fd, termios.TIOCSCTTY, 0)     # macOS needs this; Linux has already done it on open
    except OSError:
        pass
    for target in (0, 1, 2):
        os.dup2(fd, target)
    if fd > 2:
        os.close(fd)
    os.execvp(argv[0], argv)


if __name__ == "__main__":
    main()
