"""Resolve numeric PCI identifiers with the local ``pci.ids`` database.

Section 1.1: do not call an online service. The file ships with the
``pciutils`` or ``hwdata`` package.

The file holds about 2000 vendors and is 1.3 MB. One pass over it resolves
every identifier that the run needs, so the lookup takes the whole request set
at once instead of one identifier at a time.

Format::

    8086  Intel Corporation
    \t125c  Ethernet Controller I226-LM
    \t\t1028 0a5f  Subsystem name
"""

from __future__ import annotations

from ..util.context import Context

DB_PATHS = (
    "/usr/share/misc/pci.ids",
    "/usr/share/hwdata/pci.ids",
    "/usr/share/pci.ids",
    "/var/lib/pciutils/pci.ids",
)


class PciIds:
    """A resolved subset of the PCI identifier database."""

    def __init__(self) -> None:
        self.vendors: dict[str, str] = {}
        self.devices: dict[tuple[str, str], str] = {}
        self.subsystems: dict[tuple[str, str, str, str], str] = {}
        self.path: str | None = None

    def vendor(self, vendor_id: str | None) -> str | None:
        return self.vendors.get(_norm(vendor_id)) if vendor_id else None

    def device(self, vendor_id: str | None, device_id: str | None) -> str | None:
        if not vendor_id or not device_id:
            return None
        return self.devices.get((_norm(vendor_id), _norm(device_id)))

    def subsystem(
        self,
        vendor_id: str | None,
        device_id: str | None,
        sub_vendor: str | None,
        sub_device: str | None,
    ) -> str | None:
        if not all((vendor_id, device_id, sub_vendor, sub_device)):
            return None
        key = (_norm(vendor_id), _norm(device_id), _norm(sub_vendor), _norm(sub_device))
        name = self.subsystems.get(key)
        if name:
            return name
        # Fall back to the vendor of the subsystem, which is the useful half.
        vendor_name = self.vendors.get(_norm(sub_vendor))
        if vendor_name:
            return f"{vendor_name} {_norm(sub_device)}"
        return None


def _norm(value: str | None) -> str:
    value = (value or "").strip().lower()
    return value[2:] if value.startswith("0x") else value


def find_database(ctx: Context) -> str | None:
    for path in DB_PATHS:
        if ctx.fs.exists(path):
            return path
    return None


def load(ctx: Context, wanted: set[tuple[str, str]], wanted_subs: set[tuple[str, str, str, str]] | None = None) -> PciIds:
    """Resolve the given (vendor, device) pairs in one pass.

    ``wanted`` holds the pairs to resolve. Every vendor in it is resolved too.
    """
    ids = PciIds()
    path = find_database(ctx)
    ids.path = path
    if not path:
        return ids

    wanted = {(_norm(v), _norm(d)) for v, d in wanted}
    wanted_subs = {tuple(_norm(x) for x in s) for s in (wanted_subs or set())}  # type: ignore[misc]
    vendor_ids = {v for v, _ in wanted} | {s[2] for s in wanted_subs} | {s[0] for s in wanted_subs}

    text = ctx.fs.read_text(path)
    if not text:
        return ids

    current_vendor: str | None = None
    current_device: str | None = None
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        if line.startswith("C "):  # the device-class section, after the vendors
            break
        depth = len(line) - len(line.lstrip("\t"))
        body = line.strip()
        if depth == 0:
            code, _, name = body.partition("  ")
            current_vendor = code.lower()
            current_device = None
            if current_vendor in vendor_ids:
                ids.vendors[current_vendor] = name.strip()
        elif depth == 1 and current_vendor:
            code, _, name = body.partition("  ")
            current_device = code.lower()
            if (current_vendor, current_device) in wanted:
                ids.devices[(current_vendor, current_device)] = name.strip()
        elif depth == 2 and current_vendor and current_device:
            head, _, name = body.partition("  ")
            parts = head.split()
            if len(parts) == 2:
                key = (current_vendor, current_device, parts[0].lower(), parts[1].lower())
                if key in wanted_subs:
                    ids.subsystems[key] = name.strip()
    return ids


def annotate(ctx: Context, interfaces) -> PciIds:
    """Fill the vendor and device names on every interface."""
    wanted: set[tuple[str, str]] = set()
    wanted_subs: set[tuple[str, str, str, str]] = set()
    for iface in interfaces:
        pci = iface.pci
        if not pci or not pci.vendor_id or not pci.device_id:
            continue
        wanted.add((pci.vendor_id, pci.device_id))
        if pci.subsystem_vendor_id and pci.subsystem_device_id:
            wanted_subs.add((pci.vendor_id, pci.device_id, pci.subsystem_vendor_id, pci.subsystem_device_id))

    ids = load(ctx, wanted, wanted_subs)
    for iface in interfaces:
        pci = iface.pci
        if not pci:
            continue
        pci.vendor = ids.vendor(pci.vendor_id)
        pci.device = ids.device(pci.vendor_id, pci.device_id)
        pci.subsystem = ids.subsystem(
            pci.vendor_id, pci.device_id, pci.subsystem_vendor_id, pci.subsystem_device_id
        )
    return ids
