"""The PTP hardware clock.

Section 1.4 of the specification. Two sources:

* ``/sys/class/ptp/ptp<N>/`` for the clock name, the adjustment range and the
  pin count. No root needed.
* An ioctl on ``/dev/ptp<N>`` for the cross-timestamp answer and for the pin
  functions. The character device is usually owned by root.

One correction to the specification: the pin count attribute is
``n_programmable_pins`` on a current kernel, not ``n_pins``. Both names are
read, newest first.
"""

from __future__ import annotations

from ..model import Interface, PhcPin
from ..util import ptp_ioctl
from ..util.context import Context

PTP_ROOT = "/sys/class/ptp"


def collect(ctx: Context, iface: Interface) -> None:
    """Fill the PHC part of the timestamping section."""
    stamp = iface.timestamping
    index = stamp.phc_index
    if index is None or index < 0:
        return

    base = f"{PTP_ROOT}/ptp{index}"
    if not ctx.fs.exists(base):
        iface.add_error(base, "no sysfs entry for this PHC index", "failed")
        return

    stamp.clock_name = ctx.fs.read_text(f"{base}/clock_name")
    stamp.max_adjustment = ctx.fs.read_int(f"{base}/max_adjustment")
    stamp.n_ext_ts = ctx.fs.read_int(f"{base}/n_external_timestamps")
    stamp.n_per_out = ctx.fs.read_int(f"{base}/n_periodic_outputs")
    stamp.n_pins = _pin_count(ctx, base)
    stamp.phc_device = f"/dev/ptp{index}"
    stamp.phc_device_stable = stable_device(ctx, index)
    stamp.pins = _sysfs_pins(ctx, base)

    if ctx.probe_ioctl:
        _ioctl(ctx, iface)


def _pin_count(ctx: Context, base: str) -> int | None:
    for name in ("n_pins", "n_programmable_pins"):
        value = ctx.fs.read_int(f"{base}/{name}")
        if value is not None:
            return value
    return None


def _sysfs_pins(ctx: Context, base: str) -> list[PhcPin]:
    """Read ``pins/<name>``. Each file holds two numbers: function, channel.

    Function 0 is none, 1 is an external timestamp input, 2 is a periodic
    output. On an Intel i210, i225 or i226 these are the SDP pins, and they
    carry the PPS in and the PPS out.
    """
    pins: list[PhcPin] = []
    names = ctx.fs.listdir(f"{base}/pins")
    for index, name in enumerate(names):
        raw = ctx.fs.read_text(f"{base}/pins/{name}")
        func = chan = None
        if raw:
            parts = raw.split()
            if len(parts) >= 2:
                func, chan = _to_int(parts[0]), _to_int(parts[1])
            elif len(parts) == 1:
                func = _to_int(parts[0])
        pins.append(
            PhcPin(
                index=index,
                name=name,
                func=func,
                func_name=ptp_ioctl.PIN_FUNCTIONS.get(func, None) if func is not None else None,
                chan=chan,
                source="sysfs",
            )
        )
    return pins


def _ioctl(ctx: Context, iface: Interface) -> None:
    """Probe the character device. This is the hard yes or no on PTM."""
    stamp = iface.timestamping
    device = stamp.phc_device
    if not device:
        return

    result = ctx.fs.cached("ioctl", device, lambda: ptp_ioctl.probe(device))
    if not isinstance(result, dict):
        return

    error = result.get("error")
    if error:
        stamp.cross_timestamp = "unknown"
        iface.add_error(f"ioctl {device}", error, "permission" if error == "needs root" else "failed")
        return

    stamp.cross_timestamp = result.get("cross_timestamp", "unknown")
    stamp.precise_offset_ns = result.get("precise_offset_ns")

    caps = result.get("caps") or {}
    # The ioctl is the better source for these three. Sysfs can lag a driver.
    for key, attr in (("n_ext_ts", "n_ext_ts"), ("n_per_out", "n_per_out"), ("n_pins", "n_pins")):
        value = caps.get(key)
        if value is not None:
            setattr(stamp, attr, int(value))
    if caps.get("max_adj") is not None and stamp.max_adjustment is None:
        stamp.max_adjustment = int(caps["max_adj"])

    ioctl_pins = result.get("pins")
    if ioctl_pins and not stamp.pins:
        stamp.pins = [
            PhcPin(
                index=int(p["index"]),
                name=p.get("name") or None,
                func=p.get("func"),
                func_name=p.get("func_name"),
                chan=p.get("chan"),
                source="ioctl",
            )
            for p in ioctl_pins
        ]
    elif ioctl_pins:
        # Sysfs gave the names, the ioctl gives the current function.
        by_index = {int(p["index"]): p for p in ioctl_pins}
        for pin in stamp.pins:
            extra = by_index.get(pin.index)
            if extra and pin.func is None:
                pin.func = extra.get("func")
                pin.func_name = extra.get("func_name")
                pin.chan = extra.get("chan")


def stable_device(ctx: Context, index: int) -> str | None:
    """Find a ``/dev/ptp_<name>`` symlink for this clock.

    The ``ptpN`` numbering can change across a reboot. A udev rule that makes a
    named symlink gives a path that does not move. Put that path in a
    ``ts2phc`` configuration, not the number.
    """
    target = f"ptp{index}"
    for entry in ctx.fs.listdir("/dev"):
        if not entry.startswith("ptp_"):
            continue
        resolved = ctx.fs.realpath(f"/dev/{entry}")
        if resolved and resolved.rsplit("/", 1)[-1] == target:
            return f"/dev/{entry}"
    return None


def _to_int(value: str) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
