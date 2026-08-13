"""The collection orchestrator.

Two phases, and the split matters:

**Phase 1, one thread.** Everything that is shared between interfaces: the
single ``lspci -D -vvv`` pass, the DMI label map, the kernel PTM answer, the
static tables, and the ``ethtool --json`` probe. These fill the context cache.
One ``lspci`` call for the whole machine is cheaper than one call for each NIC
and each bridge above it.

**Phase 2, one worker for each interface.** Every remaining call is per port and
is dominated by process start-up, so threads help even under the GIL. The
context cache is read-only by now, so the workers do not race for it.

The interface never calls this on its event loop. It calls it on a worker.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from ..labels import Labels
from ..model import Interface, Report
from ..util.context import Context
from . import dmi, ethtool, lspci, pciids, ptp, sysfs, tsn, udev

Progress = Callable[[str, int, int], None]


def collect(
    ctx: Context,
    *,
    plan_speed_mbps: int | None = None,
    include_virtual: bool = False,
    jobs: int = 8,
    labels: Labels | None = None,
    progress: Progress | None = None,
) -> Report:
    """Collect everything and return one report."""
    from ..readiness import evaluate  # a late import keeps the import graph flat

    report = Report(collected_at=_timestamp())
    report.host = dmi.host(ctx)

    names = sysfs.list_interfaces(ctx, include_virtual=include_virtual)
    total = max(1, len(names))
    _notify(progress, "reading sysfs", 0, total)

    # -- phase 1: the shared, single-threaded work ------------------------
    report.host.ethtool_version = ethtool.version(ctx)
    if names:
        ethtool.json_supported(ctx, names[0])
    lspci.scan(ctx)
    lspci.kernel_support(ctx)
    bios_labels = dmi.onboard_labels(ctx)
    tsn.driver_table(ctx)
    tsn.device_table(ctx)

    interfaces = [sysfs.collect(ctx, name) for name in names]
    pciids.annotate(ctx, interfaces)
    _fallback_device_names(ctx, interfaces)

    # -- phase 2: one worker for each port --------------------------------
    done = 0

    def one(iface: Interface) -> Interface:
        _per_interface(ctx, iface, bios_labels)
        return iface

    if jobs > 1 and len(interfaces) > 1:
        with ThreadPoolExecutor(max_workers=min(jobs, len(interfaces))) as pool:
            for iface in pool.map(one, interfaces):
                done += 1
                _notify(progress, iface.name, done, total)
    else:
        for iface in interfaces:
            one(iface)
            done += 1
            _notify(progress, iface.name, done, total)

    if labels is not None:
        labels.apply(interfaces)

    for iface in interfaces:
        evaluate(ctx, iface, plan_speed_mbps=plan_speed_mbps)

    report.interfaces = sorted(interfaces, key=_sort_key)
    report.host.privileged = ctx.privileged
    report.host.sudo_used = ctx.runner.allow_sudo and not ctx.privileged
    return report


def _per_interface(ctx: Context, iface: Interface, bios_labels: dict[str, str]) -> None:
    udev.collect(ctx, iface)
    ethtool.driver_info(ctx, iface)
    ethtool.settings(ctx, iface)
    ethtool.timestamping(ctx, iface)
    ptp.collect(ctx, iface)
    lspci.annotate(ctx, iface)
    ethtool.features(ctx, iface)
    ethtool.module(ctx, iface)
    tsn.collect(ctx, iface)
    if iface.pci and iface.pci.bdf:
        iface.labels["bios"] = bios_labels.get(iface.pci.bdf)


def _fallback_device_names(ctx: Context, interfaces) -> None:
    """Name a device that the local ``pci.ids`` does not know.

    A distribution ``pci.ids`` is often older than the silicon in front of you.
    The i226 is missing from several shipped copies. The device table then
    supplies the name, and the source stays visible in the JSON.
    """
    table = tsn.device_table(ctx)
    for iface in interfaces:
        pci = iface.pci
        if not pci or pci.device or not pci.vendor_id or not pci.device_id:
            continue
        entry = table.get(f"{pci.vendor_id}:{pci.device_id}")
        if isinstance(entry, dict) and entry.get("name"):
            pci.device = f"{entry['name']} (from the nicscope table)"


def refresh(ctx: Context, report: Report) -> None:
    """Re-read only the volatile fields. The interface calls this at about 1 Hz.

    Section 4: cache the static facts, poll the link state. A full collection
    each second would start about thirty processes each second.
    """
    for iface in report.interfaces:
        iface.link = _merge_link(iface, sysfs.refresh_link(ctx, iface.name))


def _merge_link(iface: Interface, fresh):
    """Keep the fields that only ``ethtool`` knows, take the rest from sysfs."""
    fresh.port = iface.link.port
    fresh.autoneg = iface.link.autoneg
    fresh.supported_modes = iface.link.supported_modes
    return fresh


def _sort_key(iface: Interface) -> tuple:
    """Order by PCI address, then by name. Virtual ports go last."""
    bdf = iface.pci.bdf if iface.pci else None
    return (0 if bdf else 1, bdf or "", iface.name)


def _notify(progress: Progress | None, what: str, done: int, total: int) -> None:
    if progress is not None:
        progress(what, done, total)


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
