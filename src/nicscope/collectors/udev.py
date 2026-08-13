"""Names: the permanent address, the alternative names, the udev candidates.

Section 1.1 of the specification.

Why this matters: an interface name is not an identity. A kernel update, a BIOS
update or a moved card renames a port. The permanent MAC address does not
change, so nicscope keys the user labels on it.
"""

from __future__ import annotations

import json

from ..model import Interface
from ..util.context import Context


def collect(ctx: Context, iface: Interface) -> None:
    _from_ip(ctx, iface)
    _from_udev(ctx, iface)
    if not iface.permaddr:
        _from_ethtool(ctx, iface)


def _from_ip(ctx: Context, iface: Interface) -> None:
    """``ip -d -j link show``. Gives the alternative names and the permanent MAC."""
    result = ctx.runner.run(["ip", "-d", "-j", "link", "show", iface.name])
    if not result.ok:
        return
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        iface.add_error(f"ip -d -j link show {iface.name}", "output is not JSON", "parse")
        return
    if not isinstance(payload, list) or not payload:
        return
    entry = payload[0]
    iface.altnames = [str(n) for n in entry.get("altnames", [])]
    permaddr = entry.get("permaddr")
    if permaddr:
        iface.permaddr = str(permaddr).lower()
    if not iface.mac and entry.get("address"):
        iface.mac = str(entry["address"]).lower()
    if iface.ifindex is None and entry.get("ifindex") is not None:
        iface.ifindex = int(entry["ifindex"])


def _from_udev(ctx: Context, iface: Interface) -> None:
    """``udevadm info``. The ``ID_NET_NAME_*`` keys are the rename candidates.

    ``ID_NET_NAME_PATH`` and ``ID_NET_NAME_ONBOARD`` show what the name would be
    under a different naming policy. That explains a rename after an update.
    """
    result = ctx.runner.run(["udevadm", "info", f"/sys/class/net/{iface.name}"])
    if not result.ok:
        return
    names: dict[str, str] = {}
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if not line.startswith("E: "):
            continue
        key, _, value = line[3:].partition("=")
        if key.startswith("ID_NET_NAME") or key in ("ID_PATH", "ID_NET_DRIVER"):
            names[key] = value
    iface.udev_names = names


def _from_ethtool(ctx: Context, iface: Interface) -> None:
    """``ethtool -P``. The permanent hardware address, when ``ip`` did not give it."""
    result = ctx.runner.run(["ethtool", "-P", iface.name])
    if not result.ok:
        return
    _, _, value = result.stdout.partition(":")
    value = value.strip().lower()
    if value and value != "00:00:00:00:00:00":
        iface.permaddr = value
