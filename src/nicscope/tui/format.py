"""Shared rendering rules for the interface.

The one rule that matters, from section 4 of the specification:

    Mark every unknown value clearly. Do not print an empty cell for a field
    that failed to collect. Print ``n/a (needs root)``.

``unknown`` is a measurement result. An empty cell reads as zero, and a zero
that is really an unknown will send somebody to the wrong conclusion.
"""

from __future__ import annotations

from ..model import Interface

RESULT_COLOUR = {
    "pass": "green",
    "warn": "yellow",
    "fail": "red bold",
    "unknown": "bright_black",
}

RESULT_MARK = {"pass": "pass", "warn": "warn", "fail": "FAIL", "unknown": "unkn"}


def result(value: str) -> str:
    """A readiness result as Rich markup."""
    colour = RESULT_COLOUR.get(value, "white")
    return f"[{colour}]{RESULT_MARK.get(value, value)}[/]"


def unknown(reason: str | None = None) -> str:
    """The one way this tool prints a value it does not have."""
    return f"[bright_black]n/a ({reason})[/]" if reason else "[bright_black]n/a[/]"


def value(item, reason: str | None = None) -> str:
    if item is None:
        return unknown(reason)
    if item is True:
        return "[green]yes[/]"
    if item is False:
        return "no"
    text = str(item)
    return text if text else unknown(reason)


def tri(item, reason: str | None = None) -> str:
    """A three-state flag: yes, no, or unknown with its reason."""
    if item is None:
        return unknown(reason)
    return "[green]yes[/]" if item else "[yellow]no[/]"


def phc(iface: Interface) -> str:
    index = iface.timestamping.phc_index
    if index is None:
        return unknown("no answer")
    if index < 0:
        return "[bright_black]none[/]"
    return f"[green]{index}[/]"


def link_state(iface: Interface) -> str:
    state = iface.link.state
    if state is None:
        return unknown()
    if state == "up":
        return "[green]up[/]"
    if state == "down":
        return "[yellow]down[/]"
    return f"[bright_black]{state}[/]"


def speed(iface: Interface) -> str:
    return f"{iface.link.speed_mbps}" if iface.link.speed_mbps else unknown()


def ptm_cell(iface: Interface) -> str:
    """One cell that carries the whole PTM answer for the ports table."""
    ptm = iface.ptm
    if ptm.enabled:
        return "[green]on[/]" if ptm.chain_ok else "[yellow]on?[/]"
    if ptm.requester is None:
        return unknown("root")
    if ptm.requester:
        return "[yellow]off[/]"
    return "[bright_black]no[/]"


def why_unknown(iface: Interface, source_prefix: str) -> str | None:
    """Find the recorded reason for a field that a given source should hold."""
    for problem in iface.errors:
        if problem.source.startswith(source_prefix):
            return problem.reason
    return None


def label_of(iface: Interface) -> str:
    return iface.labels.get("user") or iface.labels.get("bios") or "[bright_black]-[/]"


def describe(iface: Interface) -> str:
    """A one-line identity for a heading."""
    parts = [iface.name]
    if iface.pci and iface.pci.bdf:
        parts.append(iface.pci.bdf)
    if iface.pci and iface.pci.device:
        parts.append(iface.pci.device)
    elif iface.driver.name:
        parts.append(iface.driver.name)
    return "  ".join(parts)
