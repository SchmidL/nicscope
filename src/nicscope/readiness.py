"""The timing readiness rules from section 3 of the specification.

Design rules that this module follows:

* **One row for each question.** The results are never merged into a score. The
  operator needs to know *which* item failed, and a score hides that.
* **``unknown`` is not ``pass``.** A check that could not run because the tool
  had no root reports ``unknown`` with the reason. It never guesses.
* **Optional is ``warn``, not ``fail``.** One-step transmit lowers jitter but
  ``ptp4l`` runs without it. A PPS input pin only matters when a GNSS receiver
  drives the card.

The severity of each row:

=========================  ========  =========================================
row                        on miss   why
=========================  ========  =========================================
``phc_present``            fail      ``ptp4l`` has nothing to discipline
``hw_tx_timestamp``        fail      a master cannot stamp its own Sync
``hw_rx_filter``           fail      a software filter adds jitter
``one_step``               warn      optional, removes the Follow_Up message
``pps_input``              warn      only ``ts2phc`` needs it
``pps_output``             warn      only a driven device needs it
``ptm_endpoint``           warn      without it the offset has a read error
``ptm_chain``              warn      the chain fails silently, so name it
``ptm_enabled``            warn      the capability exists but is off
``cross_timestamp``        warn      the measured result of the three above
``link_speed``             warn      compared against ``--plan-speed``
``pcie_link``              warn      a narrow link adds read jitter
``firmware``               warn      unverified is not the same as bad
``numa``                   warn      a remote node adds jitter, not error
=========================  ========  =========================================
"""

from __future__ import annotations

from .model import Check, Interface
from .util.context import Context

# A receive filter that the hardware applies. Anything else means the kernel
# filters in software, and that adds jitter to every timestamp.
GOOD_RX_FILTERS = ("all", "ptpv2-event", "ptpv2-l2-event", "ptpv2-l4-event")


def evaluate(ctx: Context, iface: Interface, plan_speed_mbps: int | None = None) -> list[Check]:
    """Run every rule and return the table. Also fills ``iface.commands``."""
    checks: list[Check] = [
        _phc_present(iface),
        _hw_tx(iface),
        _hw_rx(iface),
        _one_step(iface),
        _pps_input(iface),
        _pps_output(iface),
        _ptm_endpoint(iface),
        _ptm_chain(iface),
        _ptm_enabled(iface),
        _cross_timestamp(iface),
        _link_speed(iface, plan_speed_mbps),
        _pcie_link(iface),
        _firmware(ctx, iface),
        _numa(ctx, iface),
    ]
    iface.readiness = checks
    iface.commands = implied_commands(iface)
    return checks


# ------------------------------------------------------------ the rules --
def _no_phc(iface: Interface) -> str | None:
    """A short reason when a check depends on a PHC that is not there."""
    index = iface.timestamping.phc_index
    if index is None:
        return "ethtool -T gave no answer"
    if index < 0:
        return "this port has no PHC"
    return None


def _phc_present(iface: Interface) -> Check:
    index = iface.timestamping.phc_index
    if index is None:
        return Check("phc_present", "unknown", "ethtool -T gave no answer", "ptp4l needs a PHC")
    if index < 0:
        return Check("phc_present", "fail", "no PHC on this port", "ptp4l needs a PHC")
    stamp = iface.timestamping
    name = stamp.clock_name or "unnamed"
    # Name the stable symlink when udev makes one. The ptpN number can change
    # across a reboot, and a configuration file that holds the number breaks.
    device = stamp.phc_device_stable or f"/dev/ptp{index}"
    return Check("phc_present", "pass", f"{device} ({name})", "ptp4l needs a PHC")


def _hw_tx(iface: Interface) -> Check:
    types = iface.timestamping.tx_types
    why = "a master must stamp the Sync message in hardware"
    if not types:
        if iface.timestamping.phc_index is None:
            return Check("hw_tx_timestamp", "unknown", "ethtool -T gave no answer", why)
        return Check("hw_tx_timestamp", "fail", "no hardware transmit mode", why)
    if "on" in types:
        return Check("hw_tx_timestamp", "pass", ", ".join(types), why)
    return Check("hw_tx_timestamp", "fail", f"'on' missing, has {', '.join(types)}", why)


def _hw_rx(iface: Interface) -> Check:
    filters = iface.timestamping.rx_filters
    why = "a software filter adds jitter to every timestamp"
    if not filters:
        if iface.timestamping.phc_index is None:
            return Check("hw_rx_filter", "unknown", "ethtool -T gave no answer", why)
        return Check("hw_rx_filter", "fail", "no hardware receive filter", why)
    if "all" in filters:
        return Check("hw_rx_filter", "pass", "all", why)
    matched = [f for f in filters if f in GOOD_RX_FILTERS]
    if matched:
        return Check(
            "hw_rx_filter",
            "pass",
            f"{', '.join(matched)} (blocks other protocols)",
            why,
        )
    return Check("hw_rx_filter", "fail", f"only {', '.join(filters)}", why)


def _one_step(iface: Interface) -> Check:
    types = iface.timestamping.tx_types
    why = "one-step removes the Follow_Up message and lowers jitter"
    if not types:
        return Check("one_step", "unknown", _no_phc(iface) or "no transmit mode list", why)
    found = [t for t in types if t.startswith("one-step")]
    if found:
        return Check("one_step", "pass", ", ".join(found), why)
    return Check("one_step", "warn", "two-step only, which is enough for ptp4l", why)


def _pps_input(iface: Interface) -> Check:
    count = iface.timestamping.n_ext_ts
    why = "ts2phc needs an external timestamp pin for the GNSS PPS"
    if count is None:
        return Check("pps_input", "unknown", _no_phc(iface) or "the PHC reported no pin count", why)
    if count > 0:
        return Check("pps_input", "pass", f"{count} external timestamp channel(s)", why)
    return Check("pps_input", "warn", "none, so this port cannot take a PPS in", why)


def _pps_output(iface: Interface) -> Check:
    count = iface.timestamping.n_per_out
    why = "a periodic output drives another device from this clock"
    if count is None:
        return Check("pps_output", "unknown", _no_phc(iface) or "the PHC reported no pin count", why)
    if count > 0:
        return Check("pps_output", "pass", f"{count} periodic output(s)", why)
    return Check("pps_output", "warn", "none, so this port cannot drive a PPS out", why)


def _ptm_endpoint(iface: Interface) -> Check:
    why = "PTM makes the PHC to system-clock offset exact"
    value = iface.ptm.requester
    if value is None:
        return Check("ptm_endpoint", "unknown", _ptm_reason(iface), why)
    if value:
        gran = iface.ptm.granularity_ns
        detail = "PTMCap Requester" + (f", granularity {gran} ns" if gran else "")
        return Check("ptm_endpoint", "pass", detail, why)
    return Check("ptm_endpoint", "warn", "the endpoint is not a PTM requester", why)


def _ptm_chain(iface: Interface) -> Check:
    why = "PTM needs every bridge above the card to be a responder"
    value = iface.ptm.chain_ok
    if value is None:
        return Check("ptm_chain", "unknown", _ptm_reason(iface), why)
    if value:
        return Check("ptm_chain", "pass", f"{len(iface.ptm.chain)} level(s) verified", why)
    return Check("ptm_chain", "warn", _broken_link(iface), why)


def _broken_link(iface: Interface) -> str:
    """Name the level that breaks the chain. A verdict without a name is useless."""
    nodes = iface.ptm.chain
    if not nodes:
        return "no PCIe path was resolved"
    endpoint = nodes[-1]
    if endpoint.requester is False:
        return f"{endpoint.bdf} is not a requester"
    for node in nodes[:-1]:
        if node.responder is False:
            return f"{node.bdf} is not a responder"
    if not any(n.root for n in nodes if n.root is not None):
        return "no level reports PTM Root"
    return "the chain is incomplete"


def _ptm_enabled(iface: Interface) -> Check:
    why = "the capability can be present and still be off"
    value = iface.ptm.enabled
    if value is None:
        return Check("ptm_enabled", "unknown", _ptm_reason(iface), why)
    if value:
        return Check("ptm_enabled", "pass", "PTMControl Enabled", why)
    endpoint = iface.ptm.chain[-1] if iface.ptm.chain else None
    if endpoint is not None and endpoint.present is False:
        return Check("ptm_enabled", "warn", "this device has no PTM capability", why)
    if iface.ptm.kernel_support is False:
        return Check("ptm_enabled", "warn", "off, and CONFIG_PCIE_PTM is not set", why)
    return Check("ptm_enabled", "warn", "the capability is present, the kernel did not turn it on", why)


def _ptm_reason(iface: Interface) -> str:
    for problem in iface.errors:
        if problem.source.startswith("lspci"):
            return f"lspci: {problem.reason}"
    if iface.pci is None:
        return "this port is not a PCI device"
    return "no PTM data"


def _cross_timestamp(iface: Interface) -> Check:
    """The measured answer. The three PTM rows above are the paper answer."""
    why = "a precise read has no latency error, an extended read has a residual"
    value = iface.timestamping.cross_timestamp
    offset = iface.timestamping.precise_offset_ns
    if value in (None, "unknown"):
        return Check(
            "cross_timestamp",
            "unknown",
            _no_phc(iface) or "the PHC device could not be opened",
            why,
        )
    if value == "precise":
        detail = "PTP_SYS_OFFSET_PRECISE works"
        if offset is not None:
            detail += f", offset {offset} ns"
        return Check("cross_timestamp", "pass", detail, why)
    if value == "claimed":
        return Check("cross_timestamp", "warn", "the driver claims it, the ioctl did not return", why)
    return Check("cross_timestamp", "warn", "extended only, expect a residual of some hundred ns", why)


def _link_speed(iface: Interface, plan_mbps: int | None) -> Check:
    why = "the link must carry the planned rate"
    speed = iface.link.speed_mbps
    state = iface.link.state
    if state != "up":
        return Check("link_speed", "warn", f"the link is {state or 'unknown'}", why)
    if speed is None:
        return Check("link_speed", "unknown", "no speed reported", why)
    if plan_mbps is None:
        return Check("link_speed", "pass", f"{speed} Mbit/s (no plan given)", why)
    if speed >= plan_mbps:
        return Check("link_speed", "pass", f"{speed} Mbit/s, planned {plan_mbps}", why)
    return Check("link_speed", "fail", f"{speed} Mbit/s is below the planned {plan_mbps}", why)


def _pcie_link(iface: Interface) -> Check:
    why = "a link below its capability adds latency jitter to a register read"
    if iface.pci is None:
        return Check("pcie_link", "unknown", "this port is not a PCI device", why)
    link = iface.pci.link
    if link.degraded is None:
        return Check("pcie_link", "unknown", "the link numbers are incomplete", why)
    now = f"{link.speed} x{link.width}"
    top = f"{link.max_speed} x{link.max_width}"
    if link.degraded:
        return Check("pcie_link", "warn", f"{now}, the card can do {top}", why)
    return Check("pcie_link", "pass", now, why)


def _firmware(ctx: Context, iface: Interface) -> Check:
    from .collectors import tsn  # imported here to keep the rule table readable

    why = "the i225 and i226 family fixed several errata in firmware and in silicon"
    verdict, note = tsn.firmware_verdict(ctx, iface)
    iface.driver.firmware_verdict = verdict
    iface.driver.firmware_note = note
    firmware = iface.driver.firmware or "not reported"
    if verdict == "good":
        return Check("firmware", "pass", f"{firmware}: {note}", why)
    if verdict == "old":
        return Check("firmware", "warn", f"{firmware}: {note}", why)
    return Check("firmware", "unknown", f"{firmware}: {note}", why)


def _numa(ctx: Context, iface: Interface) -> Check:
    why = "a NIC on a remote NUMA node adds jitter to the capture process"
    if iface.pci is None or iface.pci.numa_node is None:
        return Check("numa", "unknown", "no NUMA node reported", why)
    nodes = _process_nodes(ctx)
    if nodes is None:
        return Check("numa", "unknown", f"the card is on node {iface.pci.numa_node}", why)
    if len(nodes) <= 1 and _node_count(ctx) <= 1:
        return Check("numa", "pass", "the machine has one NUMA node", why)
    if iface.pci.numa_node in nodes:
        return Check("numa", "pass", f"the card and this process share node {iface.pci.numa_node}", why)
    return Check(
        "numa",
        "warn",
        f"the card is on node {iface.pci.numa_node}, this process runs on {sorted(nodes)}",
        why,
    )


def _node_count(ctx: Context) -> int:
    entries = ctx.fs.listdir("/sys/devices/system/node")
    return max(1, len([e for e in entries if e.startswith("node") and e[4:].isdigit()]))


def _process_nodes(ctx: Context) -> set[int] | None:
    """Which NUMA nodes can this process run on right now."""
    try:
        import os

        cpus = os.sched_getaffinity(0)
    except (AttributeError, OSError):
        return None
    nodes: set[int] = set()
    for entry in ctx.fs.listdir("/sys/devices/system/node"):
        if not (entry.startswith("node") and entry[4:].isdigit()):
            continue
        cpulist = ctx.fs.read_text(f"/sys/devices/system/node/{entry}/cpulist")
        if cpulist and cpus & _parse_cpulist(cpulist):
            nodes.add(int(entry[4:]))
    return nodes or None


def _parse_cpulist(value: str) -> set[int]:
    """``0-3,8,12-15`` becomes a set of CPU numbers."""
    cpus: set[int] = set()
    for part in value.strip().split(","):
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            try:
                cpus.update(range(int(start), int(end) + 1))
            except ValueError:
                continue
        else:
            try:
                cpus.add(int(part))
            except ValueError:
                continue
    return cpus


# ------------------------------------------------------- implied commands --
def implied_commands(iface: Interface) -> dict[str, str]:
    """The exact linuxptp call that this interface implies.

    Section 3 asks for this as a column that the operator can copy. Every line
    is a draft. None of it is tuned for your network.
    """
    out: dict[str, str] = {}
    stamp = iface.timestamping
    if stamp.phc_index is None or stamp.phc_index < 0:
        return out

    device = stamp.phc_device_stable or stamp.phc_device or f"/dev/ptp{stamp.phc_index}"
    out["ptp4l"] = f"ptp4l -i {iface.name} -H -m"
    out["phc2sys"] = f"phc2sys -s {device} -c CLOCK_REALTIME -w -m"
    out["phc_ctl"] = f"phc_ctl {device} get"

    if (stamp.n_ext_ts or 0) > 0:
        out["ts2phc"] = f"ts2phc -c {device} -s generic -m"
    if (stamp.n_per_out or 0) > 0:
        out["pps_out"] = f"# periodic output on {device}, set the pin with testptp -L <pin>,2"
    return out
