"""One flat row for each interface. For a spreadsheet inventory."""

from __future__ import annotations

import csv as _csv
import io

from ..model import Interface, Report

COLUMNS = [
    "hostname",
    "collected_at",
    "iface",
    "mac",
    "permaddr",
    "bios_label",
    "user_label",
    "pci_bdf",
    "vendor",
    "device",
    "driver",
    "driver_version",
    "firmware",
    "link_state",
    "speed_mbps",
    "duplex",
    "mtu",
    "numa_node",
    "pcie_speed",
    "pcie_width",
    "pcie_max_speed",
    "pcie_max_width",
    "pcie_degraded",
    "phc_index",
    "phc_device",
    "clock_name",
    "tx_types",
    "rx_filters",
    "n_ext_ts",
    "n_per_out",
    "cross_timestamp",
    "ptm_requester",
    "ptm_enabled",
    "ptm_chain_ok",
    "ptm_granularity_ns",
    "etf_offload",
    "taprio_offload",
    "verdict",
    "failed_checks",
]


def render(report: Report) -> str:
    buffer = io.StringIO()
    writer = _csv.DictWriter(buffer, fieldnames=COLUMNS, lineterminator="\n")
    writer.writeheader()
    for iface in report.interfaces:
        writer.writerow(_row(report, iface))
    return buffer.getvalue()


def _row(report: Report, iface: Interface) -> dict[str, object]:
    pci = iface.pci
    link = pci.link if pci else None
    stamp = iface.timestamping
    failed = [c.check for c in iface.readiness if c.result in ("fail", "warn")]
    return {
        "hostname": report.host.hostname,
        "collected_at": report.collected_at,
        "iface": iface.name,
        "mac": iface.mac,
        "permaddr": iface.permaddr,
        "bios_label": iface.labels.get("bios"),
        "user_label": iface.labels.get("user"),
        "pci_bdf": pci.bdf if pci else None,
        "vendor": pci.vendor if pci else None,
        "device": pci.device if pci else None,
        "driver": iface.driver.name,
        "driver_version": iface.driver.version,
        "firmware": iface.driver.firmware,
        "link_state": iface.link.state,
        "speed_mbps": iface.link.speed_mbps,
        "duplex": iface.link.duplex,
        "mtu": iface.link.mtu,
        "numa_node": pci.numa_node if pci else None,
        "pcie_speed": link.speed if link else None,
        "pcie_width": link.width if link else None,
        "pcie_max_speed": link.max_speed if link else None,
        "pcie_max_width": link.max_width if link else None,
        "pcie_degraded": link.degraded if link else None,
        "phc_index": stamp.phc_index,
        "phc_device": stamp.phc_device_stable or stamp.phc_device,
        "clock_name": stamp.clock_name,
        "tx_types": " ".join(stamp.tx_types),
        "rx_filters": " ".join(stamp.rx_filters),
        "n_ext_ts": stamp.n_ext_ts,
        "n_per_out": stamp.n_per_out,
        "cross_timestamp": stamp.cross_timestamp,
        "ptm_requester": iface.ptm.requester,
        "ptm_enabled": iface.ptm.enabled,
        "ptm_chain_ok": iface.ptm.chain_ok,
        "ptm_granularity_ns": iface.ptm.granularity_ns,
        "etf_offload": iface.tsn.etf_offload,
        "taprio_offload": iface.tsn.taprio_offload,
        "verdict": iface.verdict,
        "failed_checks": " ".join(failed),
    }
