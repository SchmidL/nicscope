"""A read-only ioctl probe on ``/dev/ptp<N>``.

Section 1.5 of the specification asks for a hard yes or no on the precise
cross-timestamp, not a parse of a text file. This module gives that answer with
the standard library only. It replaces ``testptp -x`` from the kernel tree.

Three ioctls are used. All three are reads:

===========================  ====  ====================================
ioctl                        nr    what it answers
===========================  ====  ====================================
``PTP_CLOCK_GETCAPS``        1     does the driver claim cross timestamps
``PTP_PIN_GETFUNC``          6     the function and channel of one pin
``PTP_SYS_OFFSET_PRECISE``   8     does a precise read really work
===========================  ====  ====================================

``PTP_PIN_SETFUNC`` is never called. nicscope inspects. It does not configure.

The character device is owned by root on most systems. Without permission the
probe returns an error string and the caller marks the field as unknown.
"""

from __future__ import annotations

import fcntl
import os
import struct
from typing import Any

# asm-generic/ioctl.h
_IOC_NRBITS = 8
_IOC_TYPEBITS = 8
_IOC_SIZEBITS = 14
_IOC_NRSHIFT = 0
_IOC_TYPESHIFT = _IOC_NRSHIFT + _IOC_NRBITS
_IOC_SIZESHIFT = _IOC_TYPESHIFT + _IOC_TYPEBITS
_IOC_DIRSHIFT = _IOC_SIZESHIFT + _IOC_SIZEBITS
_IOC_WRITE = 1
_IOC_READ = 2

PTP_CLK_MAGIC = ord("=")


def _ioc(direction: int, magic: int, nr: int, size: int) -> int:
    return (direction << _IOC_DIRSHIFT) | (size << _IOC_SIZESHIFT) | (magic << _IOC_TYPESHIFT) | nr


# struct ptp_clock_caps: 9 ints of payload plus int rsv[11].
SIZEOF_CLOCK_CAPS = 20 * 4
# struct ptp_clock_time { __s64 sec; __u32 nsec; __u32 reserved; }
SIZEOF_CLOCK_TIME = 16
# struct ptp_sys_offset_precise: three ptp_clock_time plus unsigned int rsv[4].
SIZEOF_SYS_OFFSET_PRECISE = 3 * SIZEOF_CLOCK_TIME + 4 * 4
# struct ptp_pin_desc { char name[64]; unsigned int index, func, chan; unsigned int rsv[5]; }
SIZEOF_PIN_DESC = 64 + 3 * 4 + 5 * 4

PTP_CLOCK_GETCAPS = _ioc(_IOC_READ, PTP_CLK_MAGIC, 1, SIZEOF_CLOCK_CAPS)
PTP_PIN_GETFUNC = _ioc(_IOC_READ | _IOC_WRITE, PTP_CLK_MAGIC, 6, SIZEOF_PIN_DESC)
PTP_SYS_OFFSET_PRECISE = _ioc(_IOC_READ | _IOC_WRITE, PTP_CLK_MAGIC, 8, SIZEOF_SYS_OFFSET_PRECISE)

# Pin function numbers, from the PTP_PF_* enumeration.
PIN_FUNCTIONS = {
    0: "none",
    1: "external timestamp",
    2: "periodic output",
    3: "physical sync",
}

CAPS_FIELDS = (
    "max_adj",
    "n_alarm",
    "n_ext_ts",
    "n_per_out",
    "pps",
    "n_pins",
    "cross_timestamping",
    "adjust_phase",
    "max_phase_adj",
)


def probe(device: str, *, read_pins: bool = True) -> dict[str, Any]:
    """Probe one PHC character device.

    Returns a dictionary that is safe to put into JSON. The key ``error`` is
    present when the device could not be opened, and ``None`` otherwise.
    """
    out: dict[str, Any] = {
        "device": device,
        "error": None,
        "caps": None,
        "cross_timestamp": "unknown",
        "precise_offset_ns": None,
        "pins": None,
    }
    try:
        fd = os.open(device, os.O_RDONLY)
    except PermissionError:
        out["error"] = "needs root"
        return out
    except FileNotFoundError:
        out["error"] = "no such device"
        return out
    except OSError as exc:
        out["error"] = str(exc)
        return out

    try:
        caps = _get_caps(fd)
        out["caps"] = caps
        if caps is not None and read_pins and caps.get("n_pins"):
            out["pins"] = _get_pins(fd, int(caps["n_pins"]))
        precise = _try_precise(fd)
        out["precise_offset_ns"] = precise
        if precise is not None:
            out["cross_timestamp"] = "precise"
        elif caps is not None and caps.get("cross_timestamping"):
            # The driver claims the capability but the call did not return.
            out["cross_timestamp"] = "claimed"
        elif caps is not None:
            out["cross_timestamp"] = "extended"
    finally:
        os.close(fd)
    return out


def _get_caps(fd: int) -> dict[str, int] | None:
    buffer = bytearray(SIZEOF_CLOCK_CAPS)
    try:
        fcntl.ioctl(fd, PTP_CLOCK_GETCAPS, buffer, True)
    except OSError:
        return None
    values = struct.unpack_from("=9i", buffer, 0)
    return dict(zip(CAPS_FIELDS, values, strict=False))


def _get_pins(fd: int, count: int) -> list[dict[str, Any]] | None:
    pins: list[dict[str, Any]] = []
    for index in range(min(count, 32)):
        buffer = bytearray(SIZEOF_PIN_DESC)
        struct.pack_into("=I", buffer, 64, index)
        try:
            fcntl.ioctl(fd, PTP_PIN_GETFUNC, buffer, True)
        except OSError:
            return pins or None
        name = bytes(buffer[:64]).split(b"\x00", 1)[0].decode("utf-8", "replace")
        _index, func, chan = struct.unpack_from("=3I", buffer, 64)
        pins.append(
            {
                "index": index,
                "name": name,
                "func": func,
                "func_name": PIN_FUNCTIONS.get(func, f"unknown ({func})"),
                "chan": chan,
            }
        )
    return pins


def _try_precise(fd: int) -> int | None:
    """Do one precise cross-timestamp read.

    Returns the device-to-realtime offset in nanoseconds, or ``None`` when the
    ioctl is not supported. A returned number proves that PTM, or another
    precise path, is live right now.
    """
    buffer = bytearray(SIZEOF_SYS_OFFSET_PRECISE)
    try:
        fcntl.ioctl(fd, PTP_SYS_OFFSET_PRECISE, buffer, True)
    except OSError:
        return None
    dev_sec, dev_nsec = struct.unpack_from("=qI", buffer, 0)[:2]
    sys_sec, sys_nsec = struct.unpack_from("=qI", buffer, SIZEOF_CLOCK_TIME)[:2]
    if dev_sec == 0 and dev_nsec == 0:
        return None
    return (dev_sec - sys_sec) * 1_000_000_000 + (dev_nsec - sys_nsec)
