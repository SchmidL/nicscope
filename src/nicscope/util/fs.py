"""File-system access with record and replay.

Every sysfs read in nicscope goes through ``Filesystem``. A read that fails
returns ``None``. It never raises. A missing sysfs attribute is normal: the
attribute set depends on the driver and on the kernel version.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any, Literal

from .capture import Capture

Mode = Literal["live", "record", "replay"]


class Filesystem:
    """Read sysfs. Optionally record what was read, or replay a recording."""

    def __init__(self, mode: Mode = "live", capture: Capture | None = None) -> None:
        self.mode: Mode = mode
        self.capture = capture if capture is not None else Capture()
        if mode in ("record", "replay") and capture is None:
            raise ValueError("record and replay modes need a Capture")

    # -- primitives -------------------------------------------------------
    def read_text(self, path: str) -> str | None:
        """Return the stripped content of a file, or ``None``."""
        if self.mode == "replay":
            value = self.capture.get_fs("read", path)
            return value if isinstance(value, str) else None
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                value: str | None = handle.read().strip()
        except (OSError, UnicodeError):
            value = None
        if self.mode == "record":
            self.capture.put_fs("read", path, value)
        return value

    def read_int(self, path: str) -> int | None:
        """Return the content of a file as an integer, or ``None``."""
        raw = self.read_text(path)
        if raw is None:
            return None
        raw = raw.strip()
        try:
            return int(raw, 0) if raw.lower().startswith("0x") else int(raw)
        except ValueError:
            return None

    def listdir(self, path: str) -> list[str]:
        """Return the sorted entries of a directory. Empty list on failure."""
        if self.mode == "replay":
            value = self.capture.get_fs("list", path, [])
            return list(value) if isinstance(value, list) else []
        try:
            entries: list[str] = sorted(os.listdir(path))
        except OSError:
            entries = []
        if self.mode == "record":
            self.capture.put_fs("list", path, entries)
        return entries

    def exists(self, path: str) -> bool:
        if self.mode == "replay":
            return bool(self.capture.get_fs("exists", path, False))
        value = os.path.exists(path)
        if self.mode == "record":
            self.capture.put_fs("exists", path, value)
        return value

    def realpath(self, path: str) -> str | None:
        """Resolve a symlink. Return ``None`` when the path does not exist."""
        if self.mode == "replay":
            value = self.capture.get_fs("real", path)
            return value if isinstance(value, str) else None
        if not os.path.exists(path):
            value = None
        else:
            try:
                value = os.path.realpath(path)
            except OSError:
                value = None
        if self.mode == "record":
            self.capture.put_fs("real", path, value)
        return value

    def cached(self, op: str, path: str, produce: Callable[[], Any]) -> Any:
        """Record or replay the result of an access that is not a plain read.

        The ioctl probe on ``/dev/ptp<N>`` uses this. The probe itself cannot
        run against a recording, so the recording holds its result instead.
        """
        if self.mode == "replay":
            return self.capture.get_fs(op, path)
        value = produce()
        if self.mode == "record":
            self.capture.put_fs(op, path, value)
        return value

    # -- helpers ----------------------------------------------------------
    def basename_of_link(self, path: str) -> str | None:
        """Resolve a symlink and return the last path element.

        Used for the driver name at ``/sys/class/net/<if>/device/driver``.
        """
        target = self.realpath(path)
        return os.path.basename(target) if target else None
