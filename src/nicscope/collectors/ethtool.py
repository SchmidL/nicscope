"""Everything that comes from ``ethtool``.

Sections 1.2, 1.3, 1.4 and 1.8 of the specification.

The text output of ``ethtool`` is not a stable interface. Newer builds accept
``--json`` for some sub-commands. The policy here:

1. Probe ``--json`` once for each sub-command, on the first interface.
2. Prefer the JSON path when the probe passed and the parse gives a plausible
   result.
3. Fall back to the text parser in every other case, including an exception.

No parser may raise. A failure returns ``None`` and the caller records the
reason on the interface.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..model import DriverInfo, Interface, LinkInfo, Timestamping
from ..util.context import Context
from ..util.run import CommandResult

# The sub-commands that a recent ethtool can answer as JSON.
JSON_SUBCOMMANDS = ("-k", "-c", "-g", "-l", "-T", "--show-priv-flags")


# ---------------------------------------------------------------- version --
def version(ctx: Context) -> str | None:
    def produce() -> str | None:
        result = ctx.runner.run(["ethtool", "--version"])
        if not result.ok:
            return None
        match = re.search(r"version\s+(\S+)", result.stdout)
        return match.group(1) if match else result.stdout.strip() or None

    return ctx.cached("ethtool:version", produce)


def json_supported(ctx: Context, dev: str) -> bool:
    """Ask ethtool once whether it understands ``--json`` at all."""

    def produce() -> bool:
        result = ctx.runner.run(["ethtool", "--json", "-k", dev])
        if not result.ok:
            return False
        try:
            json.loads(result.stdout)
        except ValueError:
            return False
        return True

    return bool(ctx.cached("ethtool:json", produce))


def _run(ctx: Context, args: list[str], *, needs_root: bool = False) -> CommandResult:
    return ctx.runner.run(["ethtool", *args], needs_root=needs_root)


def _run_json(ctx: Context, dev: str, sub: str) -> Any | None:
    """Return parsed JSON for one sub-command, or ``None``."""
    if not json_supported(ctx, dev):
        return None
    result = _run(ctx, ["--json", sub, dev])
    if not result.ok:
        return None
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return None
    if isinstance(payload, list) and payload:
        return payload[0]
    return payload if isinstance(payload, dict) else None


# ----------------------------------------------------------- driver info --
def driver_info(ctx: Context, iface: Interface) -> DriverInfo:
    """``ethtool -i``. Driver, driver version, firmware version, bus."""
    result = _run(ctx, ["-i", iface.name])
    info = iface.driver
    if not result.ok:
        iface.add_error(f"ethtool -i {iface.name}", result.reason(), result.failure)
        return info
    fields = _key_values(result.stdout)
    info.name = fields.get("driver") or info.name
    info.version = _meaningful(fields.get("version"))
    info.firmware = _meaningful(fields.get("firmware-version"))
    info.expansion_rom = _meaningful(fields.get("expansion-rom-version"))
    info.bus_info = _meaningful(fields.get("bus-info"))
    return info


# ------------------------------------------------------------- settings ---
def settings(ctx: Context, iface: Interface) -> LinkInfo:
    """``ethtool <dev>``. Link modes, auto-negotiation, port type.

    Sysfs already gave speed, duplex and MTU. This call adds the port type and
    the supported link modes, which sysfs does not hold.
    """
    result = _run(ctx, [iface.name])
    link = iface.link
    if not result.stdout:
        iface.add_error(f"ethtool {iface.name}", result.reason() or "no output", result.failure)
        return link
    parsed = _parse_settings(result.stdout)
    link.port = parsed.get("port")
    link.autoneg = parsed.get("autoneg")
    link.supported_modes = parsed.get("supported_modes", [])
    if link.speed_mbps is None and parsed.get("speed_mbps") is not None:
        link.speed_mbps = parsed["speed_mbps"]
    if link.duplex is None and parsed.get("duplex"):
        link.duplex = parsed["duplex"]
    return link


def _parse_settings(text: str) -> dict[str, Any]:
    out: dict[str, Any] = {"supported_modes": []}
    in_supported = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        # ethtool may print "netlink error: ..." before the block. Ignore it.
        if line.startswith("Supported link modes:"):
            in_supported = True
            out["supported_modes"].extend(line.split(":", 1)[1].split())
            continue
        if in_supported:
            # A continuation line has no colon and is deeply indented.
            if ":" not in line and raw.startswith((" ", "\t")):
                out["supported_modes"].extend(line.split())
                continue
            in_supported = False
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "speed":
            match = re.match(r"(\d+)", value)
            out["speed_mbps"] = int(match.group(1)) if match else None
        elif key == "duplex":
            out["duplex"] = _meaningful(value)
        elif key == "port":
            out["port"] = _meaningful(value)
        elif key == "auto-negotiation":
            out["autoneg"] = _meaningful(value)
        elif key == "link detected":
            out["link_detected"] = value.lower() == "yes"
    out["supported_modes"] = [m for m in out["supported_modes"] if m not in ("Not", "reported")]
    return out


# -------------------------------------------------------- timestamping ----
def timestamping(ctx: Context, iface: Interface) -> Timestamping:
    """``ethtool -T``. The core of the timing verdict."""
    result = _run(ctx, ["-T", iface.name])
    stamp = iface.timestamping
    if not result.stdout:
        iface.add_error(f"ethtool -T {iface.name}", result.reason() or "no output", result.failure)
        return stamp
    stamp.raw = result.stdout.strip()

    parsed = None
    payload = _run_json(ctx, iface.name, "-T")
    if payload is not None:
        parsed = _parse_timestamping_json(payload)
    if not parsed:
        parsed = _parse_timestamping_text(result.stdout)

    stamp.phc_index = parsed.get("phc_index")
    stamp.tx_types = parsed.get("tx_types", [])
    stamp.rx_filters = parsed.get("rx_filters", [])
    stamp.sw_capabilities = parsed.get("sw_capabilities", [])
    stamp.hw_capabilities = parsed.get("hw_capabilities", [])
    if stamp.phc_index is not None and stamp.phc_index >= 0:
        stamp.phc_device = f"/dev/ptp{stamp.phc_index}"
    return stamp


def _parse_timestamping_text(text: str) -> dict[str, Any]:
    """Parse the ``ethtool -T`` block.

    Two shapes exist. A list under a heading::

        Hardware Transmit Timestamp Modes:
            off                   (HWTSTAMP_TX_OFF)
            on                    (HWTSTAMP_TX_ON)

    and an inline value when the set is empty::

        Hardware Transmit Timestamp Modes: none
    """
    out: dict[str, Any] = {
        "phc_index": None,
        "tx_types": [],
        "rx_filters": [],
        "sw_capabilities": [],
        "hw_capabilities": [],
    }
    headings = {
        "capabilities": "_caps",
        "hardware transmit timestamp modes": "tx_types",
        "hardware receive filter modes": "rx_filters",
    }
    caps: list[str] = []
    bucket: str | None = None

    for raw in text.splitlines():
        if not raw.strip():
            continue
        indented = raw.startswith((" ", "\t"))
        line = raw.strip()

        if not indented:
            bucket = None
            key, _, value = line.partition(":")
            key_l = key.strip().lower()
            value = value.strip()
            if key_l == "ptp hardware clock":
                # "none" is a definite answer: this port has no PHC. Keep it
                # apart from "the command gave no answer", which stays None.
                out["phc_index"] = -1 if value.lower() == "none" else _int_or_none(value)
                continue
            if key_l in headings:
                target = headings[key_l]
                if value and value.lower() != "none":
                    items = [_first_token(v) for v in value.split(",")]
                    _extend(out, caps, target, [i for i in items if i])
                elif not value:
                    bucket = target
                continue
            continue

        if bucket:
            token = _first_token(line)
            if token:
                _extend(out, caps, bucket, [token])

    out["sw_capabilities"] = sorted(c for c in caps if c.startswith("software"))
    out["hw_capabilities"] = sorted(c for c in caps if not c.startswith("software"))
    return out


def _extend(out: dict[str, Any], caps: list[str], target: str, items: list[str]) -> None:
    if target == "_caps":
        caps.extend(items)
    else:
        out[target].extend(items)


def _parse_timestamping_json(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the netlink JSON form, when the build produces it.

    The key spelling has changed between releases, so several are accepted. A
    payload that carries no recognisable PHC key is rejected, and the caller
    falls back to the text parser.
    """
    try:
        phc = payload.get("phc-index", payload.get("phc_index"))
        tx = payload.get("tx-types", payload.get("tx_types", []))
        rx = payload.get("rx-filters", payload.get("rx_filters", []))
        caps = payload.get("capabilities", payload.get("timestamping", []))
        if phc is None and not tx and not rx:
            return None
        if isinstance(caps, dict):
            caps = [k for k, v in caps.items() if v]
        tx = _as_flag_list(tx)
        rx = _as_flag_list(rx)
        caps = [str(c) for c in caps] if isinstance(caps, list) else []
        return {
            "phc_index": _int_or_none(str(phc)) if phc is not None else None,
            "tx_types": tx,
            "rx_filters": rx,
            "sw_capabilities": sorted(c for c in caps if "software" in c or c.startswith("sw")),
            "hw_capabilities": sorted(c for c in caps if not ("software" in c or c.startswith("sw"))),
        }
    except (AttributeError, TypeError, ValueError):
        return None


def _as_flag_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        return sorted(k for k, v in value.items() if v)
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


# ------------------------------------------------------------- features ---
def features(ctx: Context, iface: Interface) -> None:
    """``ethtool -k -c -g -l --show-priv-flags``. Not part of the verdict.

    These matter when you tune a capture host: coalescing adds latency, and a
    ring that is too small drops a burst.
    """
    caps = iface.features

    payload = _run_json(ctx, iface.name, "-k")
    if isinstance(payload, dict) and len(payload) > 1:
        caps.offloads = {k: v for k, v in payload.items() if k != "ifname"}
    else:
        result = _run(ctx, ["-k", iface.name])
        if result.ok:
            caps.offloads = _parse_features(result.stdout)
        else:
            iface.add_error(f"ethtool -k {iface.name}", result.reason(), result.failure)

    for sub, target in (("-c", "coalesce"), ("-g", "rings"), ("-l", "channels")):
        result = _run(ctx, [sub, iface.name])
        if result.ok:
            setattr(caps, target, _parse_sectioned(result.stdout))
        elif result.failure != "unsupported":
            iface.add_error(f"ethtool {sub} {iface.name}", result.reason(), result.failure)

    result = _run(ctx, ["--show-priv-flags", iface.name])
    if result.ok:
        caps.priv_flags = _parse_features(result.stdout)


def module(ctx: Context, iface: Interface) -> None:
    """``ethtool -m``. SFP cage ports only, and it needs CAP_NET_ADMIN."""
    if ctx.skip_root and not ctx.privileged:
        return
    result = _run(ctx, ["-m", iface.name], needs_root=True)
    if result.ok and result.stdout.strip():
        iface.features.module = _key_values(result.stdout, separator=":")
    elif result.failure == "permission":
        iface.add_error(f"ethtool -m {iface.name}", "needs root", "permission")
    # An RJ45 port has no module. That is not an error.


def _parse_features(text: str) -> dict[str, Any]:
    """``name: on`` or ``name: off [fixed]``, with indented sub-features."""
    out: dict[str, Any] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or ":" not in line or line.endswith(":"):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        fixed = "[fixed]" in value
        value = value.replace("[fixed]", "").replace("[requested on]", "").strip()
        if value in ("on", "off"):
            out[key.strip()] = {"value": value == "on", "fixed": fixed}
        elif value:
            out[key.strip()] = value
    return out


def _parse_sectioned(text: str) -> dict[str, Any]:
    """Parse output split by ``Pre-set maximums:`` and ``Current ...:``."""
    out: dict[str, Any] = {}
    section = "current"
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if low.startswith("pre-set maximums"):
            section = "max"
            continue
        if low.startswith("current hardware settings"):
            section = "current"
            continue
        if low.endswith(":") and " for " in low:  # the "Ring parameters for eth0:" header
            continue
        if ":" not in line:
            continue
        # "Adaptive RX: off  TX: off" carries two values on one line.
        pairs = re.findall(r"([A-Za-z][\w\- ]*?):\s*([^\s]+)", line)
        for key, value in pairs:
            out.setdefault(section, {})[key.strip()] = _number_or_text(value)
    return out


# ------------------------------------------------------------- blinking ---
def blink(ctx: Context, name: str, seconds: int = 10):
    """``ethtool -p``. Start the blink and return at once.

    The call blocks for the whole duration, so it never runs on the interface
    thread. The caller keeps the handle and shows a countdown.
    """
    return ctx.runner.run_background(["ethtool", "-p", name, str(seconds)], needs_root=True)


# --------------------------------------------------------------- helpers --
def _key_values(text: str, separator: str = ":") -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or separator not in line:
            continue
        key, _, value = line.partition(separator)
        key = key.strip().lower()
        if key:
            out[key] = value.strip()
    return out


def _first_token(value: str) -> str:
    """``off  (HWTSTAMP_TX_OFF)`` becomes ``off``."""
    return value.strip().split()[0] if value.strip() else ""


def _meaningful(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or value.lower() in ("n/a", "none", "unknown", "not reported"):
        return None
    return value


def _int_or_none(value: str) -> int | None:
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return None


def _number_or_text(value: str) -> Any:
    if value.lower() in ("n/a", "-"):
        return None
    try:
        return int(value)
    except ValueError:
        return value
