"""Keep secrets out of logs.

Two layers:
  * patterns: things that look like one-time sign-in codes or password prompts' answers;
  * transient secrets: anything a user typed into a sign-in prompt is remembered in memory only
    (never written anywhere) so it can be blanked out of any text before that text is logged.
"""
from __future__ import annotations

import re
import threading
from typing import List

MASK = "[redacted]"

_PATTERNS = [
    # Microsoft device-login codes as Torch prints them: "Authenticate with PIN ABCD12345 at ..."
    # The code itself must be capitals/digits with at least one capital, so ordinary words survive.
    (re.compile(r"\b((?i:PIN|code))(\s+)(?=[A-Z0-9]*[A-Z])([A-Z0-9]{6,12})\b"), r"\1\2" + MASK),
    # "password: hunter2" style echoes, should a prompt ever echo.
    (re.compile(r"(?i)(pass(?:word|phrase|code)[^:\n]{0,40}:[ \t]*)(\S+)"), r"\1" + MASK),
]


class Redactor:
    def __init__(self) -> None:
        self._secrets: List[str] = []
        self._lock = threading.Lock()

    def remember(self, secret: str) -> None:
        """Remember typed input (in memory only) so it can be blanked from logs."""
        secret = secret.strip()
        if len(secret) < 3:  # "1", "y": too short to blank without mangling ordinary text
            return
        with self._lock:
            if secret not in self._secrets:
                self._secrets.append(secret)
                self._secrets.sort(key=len, reverse=True)

    def forget_all(self) -> None:
        with self._lock:
            self._secrets = []

    def clean(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            secrets = list(self._secrets)
        for secret in secrets:
            text = text.replace(secret, MASK)
        for pattern, repl in _PATTERNS:
            text = pattern.sub(repl, text)
        return text
