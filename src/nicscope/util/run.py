"""Subprocess access with record, replay and failure classification.

A collector never calls ``subprocess`` directly. It calls ``CommandRunner.run``.
The runner returns a ``CommandResult`` for every outcome, including a missing
binary and a permission error. It does not raise.

The reason for the classification is section 2 of the specification: the tool
starts unprivileged, marks the root-only fields as unknown, and shows the reason
in the interface. To show a reason, the reason must survive the collection.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal

from .capture import Capture

Mode = Literal["live", "record", "replay"]

# Why a command produced no usable output.
FailureKind = Literal["none", "missing_tool", "permission", "unsupported", "timeout", "failed", "not_recorded"]

_PERMISSION_MARKERS = (
    "operation not permitted",
    "permission denied",
    "must be root",
    "are you root",
    "root privileges",
    "not permitted",
    "eperm",
)

_UNSUPPORTED_MARKERS = (
    "operation not supported",
    "not supported",
    "no such device",
    "invalid argument",
    "bad command line argument",
    "unexpected parameter",
)


@dataclass
class CommandResult:
    argv: list[str]
    rc: int
    stdout: str
    stderr: str
    failure: FailureKind = "none"

    @property
    def ok(self) -> bool:
        return self.failure == "none"

    @property
    def command(self) -> str:
        return " ".join(self.argv)

    def reason(self) -> str:
        """A short phrase for the interface, for example ``needs root``."""
        return {
            "none": "",
            "missing_tool": f"{self.argv[0]} not installed",
            "permission": "needs root",
            "unsupported": "not supported by this driver",
            "timeout": "command timed out",
            "failed": (self.stderr.strip().splitlines() or ["command failed"])[0][:120],
            "not_recorded": "not in the recording",
        }[self.failure]

    def to_dict(self) -> dict:
        return {"rc": self.rc, "stdout": self.stdout, "stderr": self.stderr, "failure": self.failure}


def classify(rc: int, stdout: str, stderr: str) -> FailureKind:
    """Turn an exit code and a message into a failure kind."""
    if rc == 0:
        return "none"
    blob = f"{stderr}\n{stdout}".lower()
    if any(marker in blob for marker in _PERMISSION_MARKERS):
        return "permission"
    if any(marker in blob for marker in _UNSUPPORTED_MARKERS):
        return "unsupported"
    return "failed"


class CommandRunner:
    """Run a command. Record it, or replay it from a capture."""

    def __init__(
        self,
        mode: Mode = "live",
        capture: Capture | None = None,
        timeout: float = 10.0,
        allow_sudo: bool = False,
    ) -> None:
        self.mode: Mode = mode
        self.capture = capture if capture is not None else Capture()
        self.timeout = timeout
        self.allow_sudo = allow_sudo
        self._missing: set[str] = set()
        self.log: list[CommandResult] = []
        if mode in ("record", "replay") and capture is None:
            raise ValueError("record and replay modes need a Capture")

    # -- privilege --------------------------------------------------------
    def is_root(self) -> bool:
        """True when root-only commands can run.

        In replay mode the answer comes from the recording. A capture that was
        taken as root must still report the root-only fields as collected, not
        as unknown, whoever replays it.
        """
        if self.mode == "replay":
            return bool(self.capture.meta.get("privileged", False))
        return os.geteuid() == 0

    def can_elevate(self) -> bool:
        """True when a root-only command can still run, through sudo."""
        return self.is_root() or (self.allow_sudo and shutil.which("sudo") is not None)

    # -- execution --------------------------------------------------------
    def run(self, argv: list[str], *, needs_root: bool = False, timeout: float | None = None) -> CommandResult:
        argv = list(argv)
        if self.mode == "replay":
            return self._replay(argv)

        real_argv = argv
        if needs_root and not self.is_root() and self.allow_sudo and shutil.which("sudo"):
            real_argv = ["sudo", "-n", *argv]

        if argv[0] in self._missing or shutil.which(argv[0]) is None:
            self._missing.add(argv[0])
            result = CommandResult(argv, 127, "", f"{argv[0]}: not found", "missing_tool")
            return self._finish(argv, result)

        try:
            proc = subprocess.run(
                real_argv,
                capture_output=True,
                text=True,
                timeout=timeout if timeout is not None else self.timeout,
                check=False,
                env={**os.environ, "LC_ALL": "C"},
            )
        except subprocess.TimeoutExpired:
            result = CommandResult(argv, -1, "", "timed out", "timeout")
            return self._finish(argv, result)
        except OSError as exc:  # noqa: BLE001 - any spawn failure is a failure
            result = CommandResult(argv, -1, "", str(exc), "failed")
            return self._finish(argv, result)

        failure = classify(proc.returncode, proc.stdout, proc.stderr)
        if failure != "none" and needs_root and not self.is_root():
            # A root-only command that failed without a clear message is a
            # permission problem far more often than anything else.
            if failure == "failed":
                failure = "permission"
        result = CommandResult(argv, proc.returncode, proc.stdout, proc.stderr, failure)
        return self._finish(argv, result)

    def _finish(self, argv: list[str], result: CommandResult) -> CommandResult:
        if self.mode == "record":
            self.capture.put_cmd(argv, result.to_dict())
        self.log.append(result)
        return result

    def _replay(self, argv: list[str]) -> CommandResult:
        raw = self.capture.get_cmd(argv)
        if raw is None:
            result = CommandResult(argv, -1, "", "not in the recording", "not_recorded")
        else:
            result = CommandResult(
                argv,
                int(raw.get("rc", -1)),
                raw.get("stdout", ""),
                raw.get("stderr", ""),
                raw.get("failure", "none"),
            )
        self.log.append(result)
        return result

    def run_background(self, argv: list[str], *, needs_root: bool = False) -> subprocess.Popen | None:
        """Start a command and do not wait for it.

        Used for ``ethtool -p`` (blink), which blocks for the whole duration.
        Returns ``None`` in replay mode: a recording cannot blink an LED.
        """
        if self.mode == "replay":
            return None
        if shutil.which(argv[0]) is None:
            return None
        real_argv = argv
        if needs_root and not self.is_root() and self.allow_sudo and shutil.which("sudo"):
            real_argv = ["sudo", "-n", *argv]
        try:
            return subprocess.Popen(
                real_argv,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env={**os.environ, "LC_ALL": "C"},
            )
        except OSError:
            return None
