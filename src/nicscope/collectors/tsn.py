"""Transmit scheduling: launch time and time-aware shaping.

Section 1.7 of the specification.

For a triggered measurement system the useful question is whether the NIC can
send a frame at an exact PHC time. Two offloads answer it:

* ``etf``    — earliest transmit time, one frame at one time (launch time).
* ``taprio`` — a repeating gate schedule, IEEE 802.1Qbv.

Two sources, and they answer different questions:

* ``tc qdisc show`` reports what is **configured** right now.
* the driver table reports what the driver **can** do.

A dry-run ``tc qdisc replace`` would give a harder answer, but it changes the
queue discipline of a live interface. Section 7 forbids that: this tool
inspects, it does not configure.
"""

from __future__ import annotations

import json
import re
from importlib import resources
from typing import Any

from ..model import Interface, TsnInfo
from ..util.context import Context


def driver_table(ctx: Context) -> dict[str, Any]:
    def produce() -> dict[str, Any]:
        try:
            text = resources.files("nicscope.data").joinpath("drivers.json").read_text(encoding="utf-8")
            return json.loads(text)
        except (OSError, ValueError):
            return {}

    return ctx.cached("tsn:table", produce)


def collect(ctx: Context, iface: Interface) -> TsnInfo:
    tsn = iface.tsn
    tsn.qdiscs = _qdiscs(ctx, iface)

    table = driver_table(ctx)
    entry = table.get(iface.driver.name or "", {})
    tsn.etf_offload = entry.get("etf_offload", "unknown")
    tsn.taprio_offload = entry.get("taprio_offload", "unknown")
    tsn.source = "driver_table" if entry else "unknown"

    # An offload that is configured right now beats any table.
    if "etf" in tsn.qdiscs:
        tsn.etf_offload = "yes"
        tsn.source = "tc"
    if "taprio" in tsn.qdiscs:
        tsn.taprio_offload = "yes"
        tsn.source = "tc"
    return tsn


def _qdiscs(ctx: Context, iface: Interface) -> list[str]:
    """The distinct queue disciplines attached to the interface."""
    result = ctx.runner.run(["tc", "qdisc", "show", "dev", iface.name])
    if not result.ok:
        return []
    kinds: list[str] = []
    for line in result.stdout.splitlines():
        match = re.match(r"qdisc\s+(\S+)", line.strip())
        if match and match.group(1) not in kinds:
            kinds.append(match.group(1))
    return kinds


def device_table(ctx: Context) -> dict[str, Any]:
    def produce() -> dict[str, Any]:
        try:
            text = resources.files("nicscope.data").joinpath("devices.json").read_text(encoding="utf-8")
            return json.loads(text)
        except (OSError, ValueError):
            return {}

    return ctx.cached("device:table", produce)


def _device_entry(ctx: Context, iface: Interface) -> dict[str, Any]:
    pci = iface.pci
    if not pci or not pci.vendor_id or not pci.device_id:
        return {}
    entry = device_table(ctx).get(f"{pci.vendor_id}:{pci.device_id}")
    return entry if isinstance(entry, dict) else {}


def device_note(ctx: Context, iface: Interface) -> tuple[str | None, str | None]:
    """Look up an advisory for this exact device and silicon revision.

    Returns ``(verdict, note)``. The verdict is ``good``, ``old`` or ``None``
    when the table has no entry. The table is operator-maintained: see
    ``src/nicscope/data/devices.json``.
    """
    entry = _device_entry(ctx, iface)
    if not entry:
        return None, None

    # A revision rule wins over the device default.
    revision = iface.pci.revision if iface.pci else None
    if revision is not None:
        for rule in entry.get("revisions", []):
            try:
                if int(str(rule["revision"]), 0) == revision:
                    return rule.get("verdict"), rule.get("note")
            except (KeyError, ValueError):
                continue
    return entry.get("verdict"), entry.get("note")


def firmware_verdict(ctx: Context, iface: Interface) -> tuple[str, str]:
    """Compare the reported firmware against the known-good table.

    The table ships almost empty on purpose. A firmware string that nobody has
    verified on this hardware is ``unknown``, and ``unknown`` is not ``pass``.
    Fill ``src/nicscope/data/devices.json`` from your own campaign notes.
    """
    firmware = iface.driver.firmware
    if firmware is None:
        return "unknown", "the driver does not report a firmware version"

    verdict, note = device_note(ctx, iface)
    known = _device_entry(ctx, iface).get("known_good_firmware", [])

    if known and firmware in known:
        return "good", note or "the firmware is in the known-good table"
    if known:
        return "old", f"{firmware} is not in the known-good table {known}"
    if verdict:
        return verdict, note or ""
    return "unknown", "no entry for this device in the firmware table"
