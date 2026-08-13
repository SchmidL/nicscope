"""Compare a report against an earlier one.

Section 5 of the specification. Run this before a campaign, against the export
from the last one. It catches a swapped cable, a renamed interface, a firmware
change and a card that dropped to a narrower PCIe link.

Ports are matched on the permanent MAC address, never on the interface name.
A rename is then a *change on one port*, not a port that vanished and another
that appeared. That distinction is the whole point of the mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# The fields that matter for a measurement. A change in any of them can move a
# timestamp, so each one is reported.
WATCHED: list[tuple[str, str]] = [
    ("name", "interface name"),
    ("link.state", "link state"),
    ("link.speed_mbps", "link speed (Mbit/s)"),
    ("link.duplex", "duplex"),
    ("link.mtu", "MTU"),
    ("pci.bdf", "PCI address"),
    ("pci.device", "device"),
    ("pci.numa_node", "NUMA node"),
    ("pci.link.speed", "PCIe speed"),
    ("pci.link.width", "PCIe width"),
    ("driver.name", "driver"),
    ("driver.version", "driver version"),
    ("driver.firmware", "firmware"),
    ("timestamping.phc_index", "PHC index"),
    ("timestamping.clock_name", "PHC clock name"),
    ("timestamping.n_ext_ts", "external timestamp channels"),
    ("timestamping.n_per_out", "periodic outputs"),
    ("timestamping.cross_timestamp", "cross timestamp"),
    ("ptm.enabled", "PTM enabled"),
    ("ptm.chain_ok", "PTM chain"),
    ("verdict", "readiness verdict"),
]


@dataclass
class Change:
    key: str  # the permanent MAC address
    name: str  # the interface name in the new report
    field: str
    label: str
    old: Any
    new: Any


@dataclass
class Diff:
    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[Change] = field(default_factory=list)
    old_collected_at: str | None = None
    new_collected_at: str | None = None
    old_host: str | None = None
    new_host: str | None = None

    @property
    def empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "nicscope-diff/1",
            "old": {"collected_at": self.old_collected_at, "host": self.old_host},
            "new": {"collected_at": self.new_collected_at, "host": self.new_host},
            "added": [{"key": a["key"], "name": a["name"]} for a in self.added],
            "removed": [{"key": r["key"], "name": r["name"]} for r in self.removed],
            "changed": [
                {"key": c.key, "name": c.name, "field": c.field, "old": c.old, "new": c.new}
                for c in self.changed
            ],
        }


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def compare(old: dict, new: dict) -> Diff:
    """Compare two report dictionaries, oldest first."""
    result = Diff(
        old_collected_at=old.get("collected_at"),
        new_collected_at=new.get("collected_at"),
        old_host=(old.get("host") or {}).get("hostname"),
        new_host=(new.get("host") or {}).get("hostname"),
    )

    old_map = _by_key(old)
    new_map = _by_key(new)

    for key, iface in new_map.items():
        if key not in old_map:
            result.added.append({"key": key, "name": iface.get("name")})

    for key, iface in old_map.items():
        if key not in new_map:
            result.removed.append({"key": key, "name": iface.get("name")})

    for key, fresh in new_map.items():
        stale = old_map.get(key)
        if stale is None:
            continue
        for path, label in WATCHED:
            before = _dig(stale, path)
            after = _dig(fresh, path)
            if before != after:
                result.changed.append(
                    Change(key=key, name=fresh.get("name", "?"), field=path, label=label, old=before, new=after)
                )
    return result


def _by_key(report: dict) -> dict[str, dict]:
    """Index by permanent MAC address, and fall back to the current MAC."""
    out: dict[str, dict] = {}
    for iface in report.get("interfaces", []):
        key = iface.get("permaddr") or iface.get("mac") or iface.get("name")
        if key:
            out[str(key).lower()] = iface
    return out


def _dig(payload: dict, path: str) -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def render(diff: Diff) -> str:
    """A plain-text report for a terminal or for a campaign log."""
    lines: list[str] = []
    lines.append(f"nicscope diff: {diff.old_collected_at or '?'}  ->  {diff.new_collected_at or '?'}")
    if diff.old_host != diff.new_host:
        lines.append(f"WARNING: the host name changed, {diff.old_host} -> {diff.new_host}")
    lines.append("")

    if diff.empty:
        lines.append("No change. Every port matches the earlier export.")
        return "\n".join(lines) + "\n"

    if diff.removed:
        lines.append("Ports that are gone:")
        for entry in diff.removed:
            lines.append(f"  - {entry['name']}  ({entry['key']})")
        lines.append("")

    if diff.added:
        lines.append("Ports that are new:")
        for entry in diff.added:
            lines.append(f"  + {entry['name']}  ({entry['key']})")
        lines.append("")

    if diff.changed:
        lines.append("Ports that changed:")
        current = None
        for change in diff.changed:
            if change.key != current:
                current = change.key
                lines.append(f"  {change.name}  ({change.key})")
            lines.append(f"    {change.label:<30} {_show(change.old)}  ->  {_show(change.new)}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _show(value: Any) -> str:
    if value is None:
        return "n/a"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value)
