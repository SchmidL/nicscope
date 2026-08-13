"""Host identity and the silkscreen label of an onboard port.

Section 1.8 of the specification.

The host facts come from ``/sys/class/dmi/id/``, which needs no root. Only the
port label needs ``dmidecode``, because DMI type 41 is not in sysfs.

DMI type 41 maps a PCI address to the name printed on the case, for example
``Onboard LAN 1``. It covers onboard ports only. An add-in card is not there,
and for that the LED blink is the only answer.
"""

from __future__ import annotations

from ..model import HostInfo
from ..util.context import Context

DMI_ROOT = "/sys/class/dmi/id"


def host(ctx: Context) -> HostInfo:
    """Host identity. Every field here comes from sysfs, so no root is needed."""
    info = HostInfo()
    info.hostname = ctx.fs.read_text("/proc/sys/kernel/hostname")
    info.kernel = ctx.fs.read_text("/proc/sys/kernel/osrelease")
    info.product = ctx.fs.read_text(f"{DMI_ROOT}/product_name")
    info.vendor = ctx.fs.read_text(f"{DMI_ROOT}/sys_vendor")
    info.board = ctx.fs.read_text(f"{DMI_ROOT}/board_name")
    info.bios = ctx.fs.read_text(f"{DMI_ROOT}/bios_version")
    info.bios_date = ctx.fs.read_text(f"{DMI_ROOT}/bios_date")
    info.os = _os_name(ctx)
    info.privileged = ctx.privileged
    return info


def _os_name(ctx: Context) -> str | None:
    text = ctx.fs.read_text("/etc/os-release")
    if not text:
        return None
    for line in text.splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.partition("=")[2].strip().strip('"')
    return None


def onboard_labels(ctx: Context) -> dict[str, str]:
    """Map a PCI address to the label that the BIOS assigned to it."""

    def produce() -> dict[str, str]:
        if ctx.skip_root and not ctx.privileged:
            return {}
        result = ctx.runner.run(["dmidecode", "-t", "41"], needs_root=True)
        if not result.ok:
            return {}
        return parse(result.stdout)

    return ctx.cached("dmi:labels", produce)


def parse(text: str) -> dict[str, str]:
    """Parse ``dmidecode -t 41`` into ``{bus address: reference designation}``."""
    labels: dict[str, str] = {}
    designation: str | None = None
    address: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if address and designation:
                labels[_normalise(address)] = designation
            designation = address = None
            continue
        if line.startswith("Reference Designation:"):
            designation = line.partition(":")[2].strip() or None
        elif line.startswith("Bus Address:"):
            address = line.partition(":")[2].strip() or None

    if address and designation:
        labels[_normalise(address)] = designation
    return labels


def _normalise(address: str) -> str:
    """``01:00.0`` becomes ``0000:01:00.0``."""
    address = address.strip().lower()
    return address if address.count(":") == 2 else f"0000:{address}"
