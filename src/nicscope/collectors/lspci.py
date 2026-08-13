"""PCIe topology, PTM and link quality.

Sections 1.5 and 1.6 of the specification.

PTM is a PCIe capability. ``ethtool`` cannot see it. The configuration space
that holds it needs root, so this collector has a sysfs path for the part that
sysfs exposes and an ``lspci -vvv`` path for the rest.

The chain rule matters more than the endpoint flag:

* the endpoint must be a **requester**,
* every bridge above it must be a **responder**,
* the top of the path must be a PTM **root**.

A card that reports ``Requester:+`` under a root port that is not a responder
gives no precise cross-timestamp at all.
"""

from __future__ import annotations

import re

from ..model import Interface, PcieLink, PtmNode
from ..util.context import Context

PCI_ROOT = "/sys/bus/pci/devices"

_PTM_CAP = re.compile(r"PTMCap:\s*Requester:([+-])\s*Responder:([+-])\s*Root:([+-])")
_PTM_CONTROL = re.compile(r"PTMControl:\s*Enabled:([+-])\s*RootSelected:([+-])")
_PTM_GRAN = re.compile(r"PTM(?:Effective)?ClockGranularity:\s*(.+)")
_LNKCAP = re.compile(r"LnkCap:.*?Speed\s+([\w./]+GT/s|[\w.]+GT/s|\S+),\s*Width\s+x(\d+)")
_LNKSTA = re.compile(r"LnkSta:.*?Speed\s+(\S+?)(?:\s*\(\w+\))?,\s*Width\s+x(\d+)")
_HEADER = re.compile(r"^([0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])\s+(.*)$", re.IGNORECASE)


# ------------------------------------------------------------------ scan --
def scan(ctx: Context) -> dict[str, dict]:
    """Run ``lspci -D -vvv`` once and index the blocks by address.

    One call covers every device on the machine, which is cheaper than one
    call for each NIC and each bridge above it.
    """

    def produce() -> dict[str, dict]:
        result = ctx.runner.run(["lspci", "-D", "-vvv"], needs_root=True, timeout=20.0)
        if not result.stdout:
            return {"__error__": {"reason": result.reason(), "kind": result.failure}}
        return parse(result.stdout)

    if ctx.skip_root and not ctx.privileged:
        return {"__error__": {"reason": "root commands disabled", "kind": "permission"}}
    return ctx.cached("lspci", produce)


def parse(text: str) -> dict[str, dict]:
    """Split ``lspci -vvv`` output into one record for each device."""
    devices: dict[str, dict] = {}
    current: dict | None = None
    in_ptm = False

    for raw in text.splitlines():
        if not raw.strip():
            in_ptm = False
            continue
        if not raw.startswith((" ", "\t")):
            match = _HEADER.match(raw.strip())
            in_ptm = False
            if match:
                bdf = match.group(1).lower()
                current = {
                    "bdf": bdf,
                    "description": match.group(2).strip(),
                    "ptm": None,
                    "link": {},
                    "access_denied": False,
                }
                devices[bdf] = current
            else:
                current = None
            continue

        if current is None:
            continue
        line = raw.strip()

        if "Precision Time Measurement" in line:
            in_ptm = True
            current["ptm"] = current["ptm"] or {"present": True}
            continue
        if line.startswith("Capabilities:"):
            if "access denied" in line.lower():
                current["access_denied"] = True
            in_ptm = False

        if in_ptm or line.startswith("PTM"):
            block = current["ptm"] = current["ptm"] or {"present": True}
            cap = _PTM_CAP.search(line)
            if cap:
                block["requester"] = cap.group(1) == "+"
                block["responder"] = cap.group(2) == "+"
                block["root"] = cap.group(3) == "+"
                continue
            control = _PTM_CONTROL.search(line)
            if control:
                block["enabled"] = control.group(1) == "+"
                block["root_selected"] = control.group(2) == "+"
                continue
            gran = _PTM_GRAN.search(line)
            if gran:
                block["granularity_ns"] = _granularity(gran.group(1))
                continue

        if line.startswith("LnkCap:"):
            match = _LNKCAP.search(line)
            if match:
                current["link"]["max_speed"] = _speed(match.group(1))
                current["link"]["max_width"] = int(match.group(2))
        elif line.startswith("LnkSta:"):
            match = _LNKSTA.search(line)
            if match:
                current["link"]["speed"] = _speed(match.group(1))
                current["link"]["width"] = int(match.group(2))

    return devices


def _granularity(value: str) -> int | None:
    """``4ns`` becomes 4. ``Unimplemented`` and ``Greater than 254ns`` are unknown."""
    match = re.match(r"^(\d+)\s*ns", value.strip())
    return int(match.group(1)) if match else None


def _speed(value: str) -> str:
    """Normalise ``5GT/s`` and ``5.0 GT/s`` to one spelling."""
    value = value.strip()
    match = re.match(r"^([\d.]+)\s*GT/s", value)
    if not match:
        return value
    number = float(match.group(1))
    return f"{number:g} GT/s"


# ------------------------------------------------------------ annotation --
def annotate(ctx: Context, iface: Interface) -> None:
    """Fill the PTM section and complete the PCIe link numbers."""
    ptm = iface.ptm
    ptm.kernel_support = kernel_support(ctx)

    if iface.pci is None or not iface.pci.bdf:
        ptm.source = "none"
        return

    devices = scan(ctx)
    failure = devices.get("__error__")
    path = iface.pci.path or [iface.pci.bdf]

    nodes: list[PtmNode] = []
    for position, bdf in enumerate(path):
        node = PtmNode(bdf=bdf, kind=_kind(position, len(path)))
        record = devices.get(bdf) if not failure else None
        if record:
            node.description = _short(record.get("description"))
            block = record.get("ptm")
            if block:
                node.present = True
                node.requester = block.get("requester")
                node.responder = block.get("responder")
                node.root = block.get("root")
                node.enabled = block.get("enabled")
                node.granularity_ns = block.get("granularity_ns")
            elif record.get("access_denied"):
                # The device was listed but its configuration space was not
                # readable. That is unknown, and unknown is not absent.
                node.present = None
            else:
                # The configuration space was read in full and it holds no PTM
                # capability block. That is a definite no, not a missing value,
                # and the chain rule needs the difference.
                node.present = False
                node.requester = node.responder = node.root = False
                node.enabled = False
        _sysfs_overlay(ctx, node)
        nodes.append(node)

    ptm.chain = nodes
    endpoint = nodes[-1] if nodes else None
    if endpoint:
        ptm.requester = endpoint.requester
        ptm.responder = endpoint.responder
        ptm.root = endpoint.root
        ptm.enabled = endpoint.enabled
        ptm.granularity_ns = _effective_granularity(nodes)
    ptm.chain_ok = _chain_ok(nodes)
    ptm.source = "sysfs" if failure else "lspci"

    if failure:
        iface.add_error("lspci -D -vvv", failure["reason"], failure["kind"])

    _link_from_lspci(iface, devices.get(iface.pci.bdf) if not failure else None)


def _kind(position: int, total: int) -> str:
    if total == 1:
        return "endpoint"
    if position == total - 1:
        return "endpoint"
    if position == 0:
        return "root_port"
    return "bridge"


def _sysfs_overlay(ctx: Context, node: PtmNode) -> None:
    """Prefer the sysfs answer for ``enabled`` when the kernel exposes it.

    Not every kernel has ``ptm_enabled``. Test for the file, do not assume it.
    """
    value = ctx.fs.read_text(f"{PCI_ROOT}/{node.bdf}/ptm_enabled")
    if value is not None:
        node.enabled = value.strip() in ("1", "y", "yes", "true")
        node.present = True if node.present is None else node.present


def _effective_granularity(nodes: list[PtmNode]) -> int | None:
    """The chain is only as precise as its worst hop."""
    values = [n.granularity_ns for n in nodes if n.granularity_ns]
    return max(values) if values else None


def _chain_ok(nodes: list[PtmNode]) -> bool | None:
    """Apply the chain rule. ``None`` means the answer is not knowable."""
    if not nodes:
        return None
    endpoint = nodes[-1]
    upstream = nodes[:-1]

    if endpoint.requester is None:
        return None
    if endpoint.requester is False:
        return False

    for node in upstream:
        if node.responder is None:
            return None
        if node.responder is False:
            return False

    roots = [n.root for n in nodes if n.root is not None]
    if not roots:
        # A root-complex integrated endpoint has no PCI device above it, so
        # nothing carries the Root flag. The answer is not knowable from here.
        return None if len(nodes) == 1 else False
    return any(roots)


def _link_from_lspci(iface: Interface, record: dict | None) -> None:
    """Use lspci for the PCIe link when sysfs did not give the numbers."""
    if iface.pci is None:
        return
    link = iface.pci.link or PcieLink()
    if record:
        values = record.get("link", {})
        link.speed = link.speed or values.get("speed")
        link.width = link.width if link.width is not None else values.get("width")
        link.max_speed = link.max_speed or values.get("max_speed")
        link.max_width = link.max_width if link.max_width is not None else values.get("max_width")
    if link.speed:
        link.speed = _speed(link.speed)
    if link.max_speed:
        link.max_speed = _speed(link.max_speed)
    iface.pci.link = link


def _short(description: str | None) -> str | None:
    """Reduce an lspci header line to the part that identifies the device.

    ``PCI bridge: Intel Corporation Device 7ab4 (rev 11) (prog-if 00 [Normal
    decode])`` becomes ``Intel Corporation Device 7ab4 (rev 11)``. The class and
    the programming interface are noise in a topology tree, and the square
    brackets in the tail would also read as markup in the interface.
    """
    if not description:
        return None
    _, _, rest = description.partition(": ")
    rest = (rest or description).strip()
    cut = rest.find("(prog-if")
    if cut > 0:
        rest = rest[:cut]
    return rest.strip() or None


# ------------------------------------------------------- kernel support ---
def kernel_support(ctx: Context) -> bool | None:
    """Is ``CONFIG_PCIE_PTM`` set in the running kernel."""

    def produce() -> bool | None:
        release = ctx.fs.read_text("/proc/sys/kernel/osrelease")
        if release:
            text = ctx.fs.read_text(f"/boot/config-{release}")
            if text:
                return "CONFIG_PCIE_PTM=y" in text
        result = ctx.runner.run(["zgrep", "-h", "CONFIG_PCIE_PTM", "/proc/config.gz"])
        if result.ok and result.stdout.strip():
            return "CONFIG_PCIE_PTM=y" in result.stdout
        return None

    return ctx.cached("kernel:ptm", produce)


def kernel_log(ctx: Context) -> list[str]:
    """Driver messages about PTM. ``dmesg`` often needs root."""

    def produce() -> list[str]:
        result = ctx.runner.run(["dmesg"], needs_root=True, timeout=15.0)
        if not result.ok:
            return []
        wanted = re.compile(r"\bptm\b", re.IGNORECASE)
        return [line.strip() for line in result.stdout.splitlines() if wanted.search(line)][:40]

    return ctx.cached("kernel:ptmlog", produce)
