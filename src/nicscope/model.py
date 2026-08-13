"""The data model. One dataclass for each section of the JSON schema.

Rules that hold everywhere in this module:

* An unknown value is ``None``. It is never an empty string and never a zero.
* Every ``None`` that comes from a failure has a matching entry in the nearest
  ``errors`` list. The interface reads that entry and prints the reason.
* The JSON document is the canonical output. Every other format is derived from
  it, so ``to_dict`` is the only serializer in the code base.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA = "nicscope/1"


def _clean(value: Any) -> Any:
    """Turn a dataclass tree into plain JSON types."""
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


@dataclass
class Problem:
    """One thing that could not be collected, and why."""

    source: str  # the command or the path, for example "ethtool -T enp1s0"
    reason: str  # a short phrase, for example "needs root"
    kind: str = "failed"  # missing_tool | permission | unsupported | parse | failed

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "reason": self.reason, "kind": self.kind}


@dataclass
class PcieLink:
    speed: str | None = None
    width: int | None = None
    max_speed: str | None = None
    max_width: int | None = None

    @property
    def degraded(self) -> bool | None:
        """True when the link negotiated below what the card can do."""
        if self.speed is None or self.max_speed is None or self.width is None or self.max_width is None:
            return None
        return self.speed != self.max_speed or self.width != self.max_width

    def to_dict(self) -> dict[str, Any]:
        return {
            "speed": self.speed,
            "width": self.width,
            "max_speed": self.max_speed,
            "max_width": self.max_width,
            "degraded": self.degraded,
        }


@dataclass
class PciInfo:
    bdf: str | None = None
    vendor_id: str | None = None
    device_id: str | None = None
    subsystem_vendor_id: str | None = None
    subsystem_device_id: str | None = None
    vendor: str | None = None
    device: str | None = None
    subsystem: str | None = None
    revision: int | None = None  # silicon stepping, for an errata lookup
    numa_node: int | None = None
    link: PcieLink = field(default_factory=PcieLink)
    path: list[str] = field(default_factory=list)  # root port first, endpoint last

    def to_dict(self) -> dict[str, Any]:
        return {
            "bdf": self.bdf,
            "vendor_id": self.vendor_id,
            "device_id": self.device_id,
            "revision": self.revision,
            "subsystem_vendor_id": self.subsystem_vendor_id,
            "subsystem_device_id": self.subsystem_device_id,
            "vendor": self.vendor,
            "device": self.device,
            "subsystem": self.subsystem,
            "numa_node": self.numa_node,
            "link": self.link.to_dict(),
            "path": list(self.path),
        }


@dataclass
class DriverInfo:
    name: str | None = None
    version: str | None = None
    firmware: str | None = None
    expansion_rom: str | None = None
    bus_info: str | None = None
    firmware_verdict: str | None = None  # good | old | unknown
    firmware_note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "firmware": self.firmware,
            "expansion_rom": self.expansion_rom,
            "bus_info": self.bus_info,
            "firmware_verdict": self.firmware_verdict,
            "firmware_note": self.firmware_note,
        }


@dataclass
class LinkInfo:
    state: str | None = None  # operstate: up, down, unknown
    carrier: bool | None = None
    speed_mbps: int | None = None
    duplex: str | None = None
    mtu: int | None = None
    port: str | None = None  # Twisted Pair, Fibre, Direct Attach Copper, ...
    autoneg: str | None = None
    supported_modes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "carrier": self.carrier,
            "speed_mbps": self.speed_mbps,
            "duplex": self.duplex,
            "mtu": self.mtu,
            "port": self.port,
            "autoneg": self.autoneg,
            "supported_modes": list(self.supported_modes),
        }


@dataclass
class PhcPin:
    index: int
    name: str | None = None
    func: int | None = None
    func_name: str | None = None
    chan: int | None = None
    source: str = "sysfs"  # sysfs | ioctl

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "func": self.func,
            "func_name": self.func_name,
            "chan": self.chan,
            "source": self.source,
        }


@dataclass
class Timestamping:
    phc_index: int | None = None
    phc_device: str | None = None  # /dev/ptp0
    phc_device_stable: str | None = None  # /dev/ptp_<name>, when udev makes it
    clock_name: str | None = None
    max_adjustment: int | None = None
    tx_types: list[str] = field(default_factory=list)
    rx_filters: list[str] = field(default_factory=list)
    sw_capabilities: list[str] = field(default_factory=list)
    hw_capabilities: list[str] = field(default_factory=list)
    n_pins: int | None = None
    n_ext_ts: int | None = None
    n_per_out: int | None = None
    pins: list[PhcPin] = field(default_factory=list)
    cross_timestamp: str | None = None  # precise | claimed | extended | unknown
    precise_offset_ns: int | None = None
    raw: str | None = None  # the ethtool -T block, shown on the timing screen

    def to_dict(self) -> dict[str, Any]:
        return {
            "phc_index": self.phc_index,
            "phc_device": self.phc_device,
            "phc_device_stable": self.phc_device_stable,
            "clock_name": self.clock_name,
            "max_adjustment": self.max_adjustment,
            "tx_types": list(self.tx_types),
            "rx_filters": list(self.rx_filters),
            "sw_capabilities": list(self.sw_capabilities),
            "hw_capabilities": list(self.hw_capabilities),
            "n_pins": self.n_pins,
            "n_ext_ts": self.n_ext_ts,
            "n_per_out": self.n_per_out,
            "pins": [p.to_dict() for p in self.pins],
            "cross_timestamp": self.cross_timestamp,
            "precise_offset_ns": self.precise_offset_ns,
        }


@dataclass
class PtmNode:
    """One device on the PCIe path, with its role in the PTM chain."""

    bdf: str
    description: str | None = None
    kind: str = "device"  # root_complex | root_port | bridge | endpoint
    requester: bool | None = None
    responder: bool | None = None
    root: bool | None = None
    enabled: bool | None = None
    granularity_ns: int | None = None
    present: bool | None = None  # is the PTM capability block there at all

    def to_dict(self) -> dict[str, Any]:
        return {
            "bdf": self.bdf,
            "description": self.description,
            "kind": self.kind,
            "present": self.present,
            "requester": self.requester,
            "responder": self.responder,
            "root": self.root,
            "enabled": self.enabled,
            "granularity_ns": self.granularity_ns,
        }


@dataclass
class PtmInfo:
    requester: bool | None = None
    responder: bool | None = None
    root: bool | None = None
    enabled: bool | None = None
    granularity_ns: int | None = None
    chain_ok: bool | None = None
    chain: list[PtmNode] = field(default_factory=list)
    source: str | None = None  # lspci | sysfs | none
    kernel_support: bool | None = None  # CONFIG_PCIE_PTM

    def to_dict(self) -> dict[str, Any]:
        return {
            "requester": self.requester,
            "responder": self.responder,
            "root": self.root,
            "enabled": self.enabled,
            "granularity_ns": self.granularity_ns,
            "chain_ok": self.chain_ok,
            "chain": [n.to_dict() for n in self.chain],
            "source": self.source,
            "kernel_support": self.kernel_support,
        }


@dataclass
class Features:
    """Offloads, coalescing, rings, channels and driver private flags."""

    offloads: dict[str, Any] = field(default_factory=dict)
    coalesce: dict[str, Any] = field(default_factory=dict)
    rings: dict[str, Any] = field(default_factory=dict)
    channels: dict[str, Any] = field(default_factory=dict)
    priv_flags: dict[str, Any] = field(default_factory=dict)
    module: dict[str, Any] | None = None  # ethtool -m, cage ports only

    def to_dict(self) -> dict[str, Any]:
        return {
            "offloads": self.offloads,
            "coalesce": self.coalesce,
            "rings": self.rings,
            "channels": self.channels,
            "priv_flags": self.priv_flags,
            "module": self.module,
        }


@dataclass
class TsnInfo:
    qdiscs: list[str] = field(default_factory=list)
    etf_offload: str | None = None  # yes | no | unknown
    taprio_offload: str | None = None
    source: str | None = None  # driver_table | tc

    def to_dict(self) -> dict[str, Any]:
        return {
            "qdiscs": list(self.qdiscs),
            "etf_offload": self.etf_offload,
            "taprio_offload": self.taprio_offload,
            "source": self.source,
        }


@dataclass
class Check:
    """One row of the readiness table from section 3."""

    check: str
    result: str  # pass | warn | fail | unknown
    detail: str = ""
    why: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "result": self.result, "detail": self.detail, "why": self.why}


@dataclass
class Interface:
    name: str
    mac: str | None = None
    permaddr: str | None = None
    altnames: list[str] = field(default_factory=list)
    udev_names: dict[str, str] = field(default_factory=dict)
    ifindex: int | None = None
    pci: PciInfo | None = None
    driver: DriverInfo = field(default_factory=DriverInfo)
    link: LinkInfo = field(default_factory=LinkInfo)
    timestamping: Timestamping = field(default_factory=Timestamping)
    ptm: PtmInfo = field(default_factory=PtmInfo)
    features: Features = field(default_factory=Features)
    tsn: TsnInfo = field(default_factory=TsnInfo)
    labels: dict[str, str | None] = field(default_factory=lambda: {"bios": None, "user": None})
    readiness: list[Check] = field(default_factory=list)
    commands: dict[str, str] = field(default_factory=dict)  # implied linuxptp calls
    errors: list[Problem] = field(default_factory=list)

    # -- convenience for the interface ------------------------------------
    @property
    def key(self) -> str:
        """The stable identity of a port. A name changes, a MAC does not."""
        return self.permaddr or self.mac or self.name

    @property
    def has_phc(self) -> bool:
        return self.timestamping.phc_index is not None and self.timestamping.phc_index >= 0

    @property
    def verdict(self) -> str:
        """The worst result in the readiness table."""
        results = {c.result for c in self.readiness}
        for level in ("fail", "warn", "unknown"):
            if level in results:
                return level
        return "pass" if results else "unknown"

    def add_error(self, source: str, reason: str, kind: str = "failed") -> None:
        if reason:
            self.errors.append(Problem(source, reason, kind))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mac": self.mac,
            "permaddr": self.permaddr,
            "altnames": list(self.altnames),
            "udev_names": dict(self.udev_names),
            "ifindex": self.ifindex,
            "pci": self.pci.to_dict() if self.pci else None,
            "driver": self.driver.to_dict(),
            "link": self.link.to_dict(),
            "timestamping": self.timestamping.to_dict(),
            "ptm": self.ptm.to_dict(),
            "features": self.features.to_dict(),
            "tsn": self.tsn.to_dict(),
            "labels": dict(self.labels),
            "readiness": [c.to_dict() for c in self.readiness],
            "commands": dict(self.commands),
            "errors": [e.to_dict() for e in self.errors],
            "verdict": self.verdict,
        }


@dataclass
class HostInfo:
    hostname: str | None = None
    kernel: str | None = None
    product: str | None = None
    vendor: str | None = None
    board: str | None = None
    bios: str | None = None
    bios_date: str | None = None
    os: str | None = None
    ethtool_version: str | None = None
    privileged: bool = False
    sudo_used: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "hostname": self.hostname,
            "kernel": self.kernel,
            "product": self.product,
            "vendor": self.vendor,
            "board": self.board,
            "bios": self.bios,
            "bios_date": self.bios_date,
            "os": self.os,
            "ethtool_version": self.ethtool_version,
            "privileged": self.privileged,
            "sudo_used": self.sudo_used,
        }


@dataclass
class Report:
    collected_at: str
    host: HostInfo = field(default_factory=HostInfo)
    interfaces: list[Interface] = field(default_factory=list)
    errors: list[Problem] = field(default_factory=list)
    nicscope_version: str = "0.1.0"

    def interface(self, name: str) -> Interface | None:
        for iface in self.interfaces:
            if iface.name == name:
                return iface
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "nicscope_version": self.nicscope_version,
            "collected_at": self.collected_at,
            "host": self.host.to_dict(),
            "interfaces": [i.to_dict() for i in self.interfaces],
            "errors": [e.to_dict() for e in self.errors],
        }
