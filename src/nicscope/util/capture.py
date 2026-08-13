"""Record and replay of every external read.

nicscope never touches the outside world except through two doors: the file
system (sysfs) and a subprocess (ethtool, lspci, ...). A ``Capture`` stores what
came back from each door, keyed by the request.

That gives three things:

* ``--record cap.json`` on a field machine, then ``--replay cap.json`` on a desk
  machine. The whole tool runs against the recording, with no hardware.
* Unit tests parse from a small JSON file, not from a live NIC.
* A bug report is one file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Capture:
    """A recording of file-system reads and command runs."""

    fs: dict[str, Any] = field(default_factory=dict)
    commands: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)

    # -- file system ------------------------------------------------------
    @staticmethod
    def fs_key(op: str, path: str) -> str:
        return f"{op}:{path}"

    def put_fs(self, op: str, path: str, value: Any) -> None:
        self.fs[self.fs_key(op, path)] = value

    def get_fs(self, op: str, path: str, default: Any = None) -> Any:
        return self.fs.get(self.fs_key(op, path), default)

    def has_fs(self, op: str, path: str) -> bool:
        return self.fs_key(op, path) in self.fs

    # -- commands ---------------------------------------------------------
    @staticmethod
    def cmd_key(argv: list[str]) -> str:
        return " ".join(argv)

    def put_cmd(self, argv: list[str], value: dict[str, Any]) -> None:
        self.commands[self.cmd_key(argv)] = value

    def get_cmd(self, argv: list[str]) -> dict[str, Any] | None:
        return self.commands.get(self.cmd_key(argv))

    # -- persistence ------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {"schema": "nicscope-capture/1", "meta": self.meta, "fs": self.fs, "commands": self.commands}

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=1, sort_keys=True)
            handle.write("\n")

    @classmethod
    def load(cls, path: str) -> Capture:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
        return cls(fs=raw.get("fs", {}), commands=raw.get("commands", {}), meta=raw.get("meta", {}))
