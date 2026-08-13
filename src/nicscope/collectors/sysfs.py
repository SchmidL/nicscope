"""Interface inventory from sysfs.

Section 1.1 of the specification. Sysfs is stable and needs no root, so it is
the first source for every fact that it holds.
"""

from __future__ import annotations

import os

from ..model import Interface, LinkInfo, PcieLink, PciInfo
from ..util.context import Context

NET_ROOT = "/sys/class/net"
PCI_ROOT = "/sys/bus/pci/devices"

# A virtual interface has no hardware to inspect.
VIRTUAL_PREFIXES = ("lo", "docker", "veth", "virbr", "br-", "tun", "tap", "wg", "vxlan", "bond", "dummy")


def list_interfaces(ctx: Context, include_virtual: bool = False) -> list[str]:
    """Return the interface names to inspect."""
    names = [n for n in ctx.fs.listdir(NET_ROOT) if n]
    if not include_virtual:
        names = [n for n in names if not _is_virtual(ctx, n)]
    if ctx.only is not None:
        wanted = set(ctx.only)
        names = [n for n in names if n in wanted]
    return names


def _is_virtual(ctx: Context, name: str) -> bool:
    if name == "lo":
        return True
    # A physical port has a device link. A bridge or a veth does not.
    if ctx.fs.exists(f"{NET_ROOT}/{name}/device"):
        return False
    return name.startswith(VIRTUAL_PREFIXES)


def collect(ctx: Context, name: str) -> Interface:
    """Build an interface from sysfs alone. Later collectors add to it."""
    base = f"{NET_ROOT}/{name}"
    iface = Interface(name=name)
    iface.mac = ctx.fs.read_text(f"{base}/address")
    iface.ifindex = ctx.fs.read_int(f"{base}/ifindex")

    carrier = ctx.fs.read_int(f"{base}/carrier")
    iface.link = LinkInfo(
        state=ctx.fs.read_text(f"{base}/operstate"),
        carrier=None if carrier is None else bool(carrier),
        speed_mbps=_positive(ctx.fs.read_int(f"{base}/speed")),
        duplex=_meaningful(ctx.fs.read_text(f"{base}/duplex")),
        mtu=ctx.fs.read_int(f"{base}/mtu"),
    )

    iface.pci = _pci(ctx, name)
    if iface.pci is None:
        # Not an error. A USB adapter or a Hyper-V vNIC has no PCI address.
        driver = ctx.fs.basename_of_link(f"{base}/device/driver")
        if driver:
            iface.driver.name = driver
    return iface


def refresh_link(ctx: Context, name: str) -> LinkInfo:
    """Re-read only the fields that change while the tool runs.

    The interface polls this at about 1 Hz. Everything else is cached.
    """
    base = f"{NET_ROOT}/{name}"
    carrier = ctx.fs.read_int(f"{base}/carrier")
    return LinkInfo(
        state=ctx.fs.read_text(f"{base}/operstate"),
        carrier=None if carrier is None else bool(carrier),
        speed_mbps=_positive(ctx.fs.read_int(f"{base}/speed")),
        duplex=_meaningful(ctx.fs.read_text(f"{base}/duplex")),
        mtu=ctx.fs.read_int(f"{base}/mtu"),
    )


def _positive(value: int | None) -> int | None:
    """A down port reports a speed of -1. That is unknown, not a speed."""
    if value is None or value < 0:
        return None
    return value


def _meaningful(value: str | None) -> str | None:
    if value is None or value.lower() in ("unknown", ""):
        return None
    return value


def _pci(ctx: Context, name: str) -> PciInfo | None:
    device_link = f"{NET_ROOT}/{name}/device"
    target = ctx.fs.realpath(device_link)
    if not target:
        return None
    bdf = os.path.basename(target)
    if not _looks_like_bdf(bdf):
        return None

    info = PciInfo(bdf=bdf)
    info.vendor_id = _hex_id(ctx.fs.read_text(f"{device_link}/vendor"))
    info.device_id = _hex_id(ctx.fs.read_text(f"{device_link}/device"))
    info.subsystem_vendor_id = _hex_id(ctx.fs.read_text(f"{device_link}/subsystem_vendor"))
    info.subsystem_device_id = _hex_id(ctx.fs.read_text(f"{device_link}/subsystem_device"))
    info.revision = ctx.fs.read_int(f"{device_link}/revision")

    numa = ctx.fs.read_int(f"{device_link}/numa_node")
    info.numa_node = None if numa is None or numa < 0 else numa

    info.link = PcieLink(
        speed=_meaningful(ctx.fs.read_text(f"{device_link}/current_link_speed")),
        width=ctx.fs.read_int(f"{device_link}/current_link_width"),
        max_speed=_meaningful(ctx.fs.read_text(f"{device_link}/max_link_speed")),
        max_width=ctx.fs.read_int(f"{device_link}/max_link_width"),
    )
    info.path = pcie_path(ctx, bdf)
    return info


def pcie_path(ctx: Context, bdf: str) -> list[str]:
    """Walk from the endpoint up to the root complex.

    Returns the addresses from the root port down to the endpoint. The PTM
    chain check needs this order, because the rule differs by level.
    """
    chain: list[str] = []
    current = ctx.fs.realpath(f"{PCI_ROOT}/{bdf}")
    guard = 0
    while current and guard < 16:
        guard += 1
        leaf = os.path.basename(current)
        if not _looks_like_bdf(leaf):
            break
        chain.append(leaf)
        current = os.path.dirname(current)
    chain.reverse()
    return chain


def driver_of(ctx: Context, name: str) -> str | None:
    return ctx.fs.basename_of_link(f"{NET_ROOT}/{name}/device/driver")


def _looks_like_bdf(value: str) -> bool:
    """Match ``0000:01:00.0``."""
    parts = value.split(":")
    if len(parts) != 3:
        return False
    domain, bus, rest = parts
    if "." not in rest:
        return False
    device, function = rest.split(".", 1)
    try:
        int(domain, 16), int(bus, 16), int(device, 16), int(function, 16)
    except ValueError:
        return False
    return True


def _hex_id(value: str | None) -> str | None:
    """``0x8086`` becomes ``8086``."""
    if not value:
        return None
    value = value.strip().lower()
    return value[2:] if value.startswith("0x") else value
