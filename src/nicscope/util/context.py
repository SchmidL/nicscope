"""The collection context.

One object carries the file system, the command runner and the options into
every collector. Nothing in ``collectors/`` reaches for a global.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .capture import Capture
from .fs import Filesystem
from .run import CommandRunner


@dataclass
class Context:
    fs: Filesystem
    runner: CommandRunner
    capture: Capture
    only: list[str] | None = None  # restrict to these interface names
    skip_root: bool = False  # never try a root-only command
    probe_ioctl: bool = True  # open /dev/ptp<N> and probe
    _cache: dict[str, Any] = field(default_factory=dict)

    def cached(self, key: str, produce) -> Any:
        """Memoize a value that several collectors need, for example lspci."""
        if key not in self._cache:
            self._cache[key] = produce()
        return self._cache[key]

    @property
    def privileged(self) -> bool:
        return self.runner.is_root()

    def wants(self, name: str) -> bool:
        return self.only is None or name in self.only


def make_context(
    *,
    record: str | None = None,
    replay: str | None = None,
    only: list[str] | None = None,
    allow_sudo: bool = False,
    skip_root: bool = False,
    probe_ioctl: bool = True,
    timeout: float = 10.0,
) -> Context:
    """Build a context for a live run, a recording run or a replay run."""
    if replay:
        capture = Capture.load(replay)
        mode = "replay"
    elif record:
        capture = Capture()
        mode = "record"
    else:
        capture = Capture()
        mode = "live"
    return Context(
        fs=Filesystem(mode=mode, capture=capture),
        runner=CommandRunner(mode=mode, capture=capture, timeout=timeout, allow_sudo=allow_sudo),
        capture=capture,
        only=only,
        skip_root=skip_root,
        probe_ioctl=probe_ioctl,
    )
