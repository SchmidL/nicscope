"""A report for the campaign logbook.

One section for each interface, plus the readiness table. The output is plain
CommonMark, so it pastes into an Obsidian vault or a wiki without change.
"""

from __future__ import annotations

from ..model import Interface, Report

MARK = {"pass": "pass", "warn": "warn", "fail": "FAIL", "unknown": "unknown"}


def render(report: Report) -> str:
    lines: list[str] = []
    host = report.host

    lines.append(f"# NIC inspection: {host.hostname or 'unknown host'}")
    lines.append("")
    lines.append(f"Collected at {report.collected_at} by nicscope {report.nicscope_version}.")
    lines.append("")

    lines.append("## Host")
    lines.append("")
    lines.extend(
        _table(
            ["Item", "Value"],
            [
                ["Hostname", host.hostname],
                ["Product", _join(host.vendor, host.product)],
                ["Board", host.board],
                ["BIOS", _join(host.bios, host.bios_date)],
                ["Operating system", host.os],
                ["Kernel", host.kernel],
                ["ethtool", host.ethtool_version],
                ["Run as root", "yes" if host.privileged else "no"],
            ],
        )
    )
    lines.append("")

    if not host.privileged:
        lines.append("> The run was unprivileged. Every field marked `n/a (needs root)` is")
        lines.append("> unknown, not absent. Run again under `sudo` to complete it.")
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    rows = []
    for iface in report.interfaces:
        pci = iface.pci
        rows.append(
            [
                f"`{iface.name}`",
                pci.bdf if pci else "-",
                iface.driver.name,
                _speed(iface),
                iface.link.state,
                _phc(iface),
                _tri(iface.ptm.enabled),
                iface.labels.get("bios") or iface.labels.get("user") or "-",
                MARK.get(iface.verdict, iface.verdict),
            ]
        )
    lines.extend(
        _table(["Iface", "PCI", "Driver", "Speed", "Link", "PHC", "PTM", "Label", "Verdict"], rows)
    )
    lines.append("")

    for iface in report.interfaces:
        lines.extend(_interface(iface))

    if report.errors:
        lines.append("## Collection errors")
        lines.append("")
        lines.extend(_table(["Source", "Reason"], [[f"`{e.source}`", e.reason] for e in report.errors]))
        lines.append("")

    return "\n".join(lines) + "\n"


def _interface(iface: Interface) -> list[str]:
    lines: list[str] = []
    pci = iface.pci
    stamp = iface.timestamping

    title = f"## `{iface.name}`"
    label = iface.labels.get("user") or iface.labels.get("bios")
    if label:
        title += f" — {label}"
    lines.append(title)
    lines.append("")

    lines.extend(
        _table(
            ["Item", "Value"],
            [
                ["MAC address", iface.mac],
                ["Permanent MAC address", iface.permaddr],
                ["PCI address", pci.bdf if pci else None],
                ["Device", _join(pci.vendor, pci.device) if pci else None],
                ["Subsystem", pci.subsystem if pci else None],
                ["Driver", _join(iface.driver.name, iface.driver.version)],
                ["Firmware", iface.driver.firmware],
                ["NUMA node", pci.numa_node if pci else None],
                ["Link", f"{iface.link.state}, {_speed(iface)}, {iface.link.duplex or '?'} duplex"],
                ["MTU", iface.link.mtu],
                ["Port type", iface.link.port],
                ["PCIe link", _pcie(iface)],
                ["BIOS label", iface.labels.get("bios")],
            ],
        )
    )
    lines.append("")

    lines.append("### Timestamping")
    lines.append("")
    lines.extend(
        _table(
            ["Item", "Value"],
            [
                ["PHC index", stamp.phc_index],
                ["PHC device", stamp.phc_device_stable or stamp.phc_device],
                ["Clock name", stamp.clock_name],
                ["Maximum adjustment", _ppb(stamp.max_adjustment)],
                ["Transmit modes", ", ".join(stamp.tx_types) or None],
                ["Receive filters", ", ".join(stamp.rx_filters) or None],
                ["External timestamp channels", stamp.n_ext_ts],
                ["Periodic outputs", stamp.n_per_out],
                ["Pins", stamp.n_pins],
                ["Cross timestamp", stamp.cross_timestamp],
            ],
        )
    )
    lines.append("")

    if stamp.pins:
        lines.append("Pins:")
        lines.append("")
        lines.extend(
            _table(
                ["Index", "Name", "Function", "Channel"],
                [[p.index, p.name, p.func_name or p.func, p.chan] for p in stamp.pins],
            )
        )
        lines.append("")

    if iface.ptm.chain:
        lines.append("### PCIe path and PTM")
        lines.append("")
        lines.extend(
            _table(
                ["Address", "Role", "Device", "Requester", "Responder", "Root", "Enabled", "Granularity"],
                [
                    [
                        f"`{n.bdf}`",
                        n.kind.replace("_", " "),
                        n.description or "-",
                        _tri(n.requester),
                        _tri(n.responder),
                        _tri(n.root),
                        _tri(n.enabled),
                        f"{n.granularity_ns} ns" if n.granularity_ns else "-",
                    ]
                    for n in iface.ptm.chain
                ],
            )
        )
        lines.append("")

    lines.append("### Readiness")
    lines.append("")
    lines.extend(
        _table(
            ["Check", "Result", "Detail"],
            [[c.check, MARK.get(c.result, c.result), c.detail] for c in iface.readiness],
        )
    )
    lines.append("")

    if iface.commands:
        lines.append("Implied linuxptp calls. Every line is a draft, not a tuned configuration.")
        lines.append("")
        lines.append("```sh")
        for value in iface.commands.values():
            lines.append(value)
        lines.append("```")
        lines.append("")

    if iface.errors:
        lines.append("Fields that could not be collected:")
        lines.append("")
        lines.extend(
            _table(["Source", "Reason"], [[f"`{e.source}`", e.reason] for e in iface.errors])
        )
        lines.append("")

    return lines


# ------------------------------------------------------------- helpers ----
def _table(header: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * len(header)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return out


def _cell(value) -> str:
    """An unknown value is never an empty cell. An empty cell reads as zero."""
    if value is None:
        return "n/a"
    if value is True:
        return "yes"
    if value is False:
        return "no"
    return str(value).replace("|", "\\|")


def _tri(value) -> str:
    return {None: "n/a", True: "yes", False: "no"}.get(value, str(value))


def _join(*parts) -> str | None:
    kept = [str(p) for p in parts if p]
    return " ".join(kept) if kept else None


def _speed(iface: Interface) -> str:
    return f"{iface.link.speed_mbps} Mbit/s" if iface.link.speed_mbps else "n/a"


def _phc(iface: Interface) -> str:
    index = iface.timestamping.phc_index
    if index is None:
        return "n/a"
    return str(index) if index >= 0 else "none"


def _pcie(iface: Interface) -> str | None:
    if not iface.pci:
        return None
    link = iface.pci.link
    if not link.speed and not link.max_speed:
        return None
    now = f"{link.speed or '?'} x{link.width if link.width is not None else '?'}"
    top = f"{link.max_speed or '?'} x{link.max_width if link.max_width is not None else '?'}"
    if link.degraded:
        return f"{now} (the card can do {top})"
    return now


def _ppb(value) -> str | None:
    return f"{value} ppb" if value is not None else None
