#!/usr/bin/env python3
"""Build the synthetic capture that the unit tests run against.

The machine in this fixture does not exist. It is assembled to exercise every
branch that a real measurement host can take, and a real host rarely shows all
of them at once:

===========  ==============================================================
``enp1s0``   Intel I226-LM. PHC 0, SDP pins, PTM requester, chain complete
             and enabled. This is the port that a timing plan wants.
``enp2s0``   Intel I226-V. PHC 1, PTM requester, but the root port above it
             is **not** a responder. The chain is broken, and the tool must
             name the level that breaks it.
``enp3s0``   Intel I210 behind a PCIe switch. PHC 2, no PTM anywhere, and a
             PCIe link that negotiated below its capability.
``eno1``     Intel I219. No PHC at all, and no PTM.
===========  ==============================================================

Regenerate with::

    python3 tests/fixtures/build_synthetic.py

A capture from real hardware comes from ``nicscope --record capture.json``.
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "synthetic.capture.json")

KERNEL = "6.8.0-40-generic"
HOSTNAME = "meas01"

fs: dict[str, object] = {}
commands: dict[str, dict] = {}


def read(path: str, value):
    fs[f"read:{path}"] = value


def listing(path: str, value):
    fs[f"list:{path}"] = value


def exists(path: str, value=True):
    fs[f"exists:{path}"] = value


def real(path: str, value):
    fs[f"real:{path}"] = value


def cmd(argv: str, stdout: str, rc: int = 0, stderr: str = "", failure: str = "none"):
    commands[argv] = {"rc": rc, "stdout": stdout, "stderr": stderr, "failure": failure}


# --------------------------------------------------------------- the host --
read("/proc/sys/kernel/hostname", HOSTNAME)
read("/proc/sys/kernel/osrelease", KERNEL)
read("/sys/class/dmi/id/product_name", "Nuvo-7000")
read("/sys/class/dmi/id/sys_vendor", "Neousys Technology")
read("/sys/class/dmi/id/board_name", "NuMB-7000")
read("/sys/class/dmi/id/bios_version", "V1.07")
read("/sys/class/dmi/id/bios_date", "05/14/2025")
read("/etc/os-release", 'PRETTY_NAME="Ubuntu 24.04.4 LTS"\nID=ubuntu\n')
read(f"/boot/config-{KERNEL}", "CONFIG_PCIE_PTM=y\nCONFIG_PTP_1588_CLOCK=y\n")

listing("/sys/devices/system/node", ["node0"])
read("/sys/devices/system/node/node0/cpulist", "0-7")

# One NUMA node, so the NUMA check passes for every card on node 0.

# ------------------------------------------------------------- interfaces --
listing("/sys/class/net", ["eno1", "enp1s0", "enp2s0", "enp3s0", "lo"])

PORTS = [
    {
        "name": "enp1s0",
        "mac": "aa:bb:cc:00:01:00",
        "bdf": "0000:01:00.0",
        "path": ["0000:00:1c.4", "0000:01:00.0"],
        "vendor": "0x8086",
        "device": "0x125b",
        "revision": "0x04",
        "speed": 1000,
        "state": "up",
        "carrier": 1,
        "phc": 0,
        "driver": "igc",
        "firmware": "2017:888d",
        "pcie": ("5.0 GT/s PCIe", 1, "5.0 GT/s PCIe", 1),
    },
    {
        "name": "enp2s0",
        "mac": "aa:bb:cc:00:02:00",
        "bdf": "0000:02:00.0",
        "path": ["0000:00:1c.5", "0000:02:00.0"],
        "vendor": "0x8086",
        "device": "0x125c",
        "revision": "0x04",
        "speed": None,
        "state": "down",
        "carrier": 0,
        "phc": 1,
        "driver": "igc",
        "firmware": "2017:888d",
        "pcie": ("5.0 GT/s PCIe", 1, "5.0 GT/s PCIe", 1),
    },
    {
        "name": "enp3s0",
        "mac": "aa:bb:cc:00:03:00",
        "bdf": "0000:04:00.0",
        "path": ["0000:00:1d.0", "0000:03:00.0", "0000:04:00.0"],
        "vendor": "0x8086",
        "device": "0x1533",
        "revision": "0x03",
        "speed": 1000,
        "state": "up",
        "carrier": 1,
        "phc": 2,
        "driver": "igb",
        "firmware": "3.25, 0x800005c0",
        "pcie": ("2.5 GT/s PCIe", 1, "2.5 GT/s PCIe", 4),  # narrower than capable
    },
    {
        "name": "eno1",
        "mac": "aa:bb:cc:00:00:01",
        "bdf": "0000:00:1f.6",
        "path": ["0000:00:1f.6"],
        "vendor": "0x8086",
        "device": "0x15fb",
        "revision": "0x11",
        "speed": 1000,
        "state": "up",
        "carrier": 1,
        "phc": None,
        "driver": "e1000e",
        "firmware": "0.6-4",
        "pcie": (None, None, None, None),
    },
]

for port in PORTS:
    name, bdf = port["name"], port["bdf"]
    base = f"/sys/class/net/{name}"
    device_path = "/sys/devices/pci0000:00/" + "/".join(port["path"])

    read(f"{base}/address", port["mac"])
    read(f"{base}/ifindex", str(2 + PORTS.index(port)))
    read(f"{base}/carrier", str(port["carrier"]))
    read(f"{base}/operstate", port["state"])
    read(f"{base}/speed", str(port["speed"]) if port["speed"] else "-1")
    read(f"{base}/duplex", "full" if port["state"] == "up" else "unknown")
    read(f"{base}/mtu", "1500")
    exists(f"{base}/device", True)
    real(f"{base}/device", device_path)
    real(f"/sys/bus/pci/devices/{bdf}", device_path)
    real(f"{base}/device/driver", f"/sys/bus/pci/drivers/{port['driver']}")

    read(f"{base}/device/vendor", port["vendor"])
    read(f"{base}/device/device", port["device"])
    read(f"{base}/device/subsystem_vendor", "0x8086")
    read(f"{base}/device/subsystem_device", "0x0000")
    read(f"{base}/device/revision", port["revision"])
    read(f"{base}/device/numa_node", "0")
    speed, width, max_speed, max_width = port["pcie"]
    read(f"{base}/device/current_link_speed", speed)
    read(f"{base}/device/current_link_width", str(width) if width else None)
    read(f"{base}/device/max_link_speed", max_speed)
    read(f"{base}/device/max_link_width", str(max_width) if max_width else None)

exists("/sys/class/net/lo/device", False)

# ---------------------------------------------------------------- the PHCs --
PHCS = {
    0: {"name": "igc-ptp", "ext": 1, "per": 2, "pins": {"SDP0": "1 0", "SDP1": "2 0", "SDP2": "0 0", "SDP3": "0 0"}},
    1: {"name": "igc-ptp", "ext": 1, "per": 2, "pins": {"SDP0": "0 0", "SDP1": "0 0", "SDP2": "0 0", "SDP3": "0 0"}},
    2: {"name": "igb-ptp", "ext": 1, "per": 1, "pins": {"SDP0": "0 0", "SDP1": "0 0"}},
}
for index, phc in PHCS.items():
    base = f"/sys/class/ptp/ptp{index}"
    exists(base, True)
    read(f"{base}/clock_name", phc["name"])
    read(f"{base}/max_adjustment", "62499999")
    read(f"{base}/n_external_timestamps", str(phc["ext"]))
    read(f"{base}/n_periodic_outputs", str(phc["per"]))
    read(f"{base}/n_pins", None)  # a current kernel uses the name below
    read(f"{base}/n_programmable_pins", str(len(phc["pins"])))
    listing(f"{base}/pins", sorted(phc["pins"]))
    for pin_name, value in phc["pins"].items():
        read(f"{base}/pins/{pin_name}", value)

listing("/dev", ["null", "ptp0", "ptp1", "ptp2", "ptp_grandmaster", "zero"])
real("/dev/ptp_grandmaster", "/dev/ptp0")

# The ioctl probe. enp1s0 has PTM, so the precise read returns. enp2s0 has a
# broken chain, so the driver claims the capability and the call does not work.
# enp3s0 has no PTM at all, so the kernel falls back to the extended read.
fs["ioctl:/dev/ptp0"] = {
    "device": "/dev/ptp0",
    "error": None,
    "caps": {"max_adj": 62499999, "n_alarm": 0, "n_ext_ts": 1, "n_per_out": 2, "pps": 1,
             "n_pins": 4, "cross_timestamping": 1, "adjust_phase": 0, "max_phase_adj": 0},
    "cross_timestamp": "precise",
    "precise_offset_ns": -1372,
    "pins": [
        {"index": 0, "name": "SDP0", "func": 1, "func_name": "external timestamp", "chan": 0},
        {"index": 1, "name": "SDP1", "func": 2, "func_name": "periodic output", "chan": 0},
        {"index": 2, "name": "SDP2", "func": 0, "func_name": "none", "chan": 0},
        {"index": 3, "name": "SDP3", "func": 0, "func_name": "none", "chan": 0},
    ],
}
fs["ioctl:/dev/ptp1"] = {
    "device": "/dev/ptp1",
    "error": None,
    "caps": {"max_adj": 62499999, "n_alarm": 0, "n_ext_ts": 1, "n_per_out": 2, "pps": 1,
             "n_pins": 4, "cross_timestamping": 1, "adjust_phase": 0, "max_phase_adj": 0},
    "cross_timestamp": "claimed",
    "precise_offset_ns": None,
    "pins": None,
}
fs["ioctl:/dev/ptp2"] = {
    "device": "/dev/ptp2",
    "error": None,
    "caps": {"max_adj": 62499999, "n_alarm": 0, "n_ext_ts": 1, "n_per_out": 1, "pps": 1,
             "n_pins": 2, "cross_timestamping": 0, "adjust_phase": 0, "max_phase_adj": 0},
    "cross_timestamp": "extended",
    "precise_offset_ns": None,
    "pins": None,
}

# ------------------------------------------------------------- pci.ids ----
exists("/usr/share/misc/pci.ids", True)
exists("/usr/share/hwdata/pci.ids", False)
read(
    "/usr/share/misc/pci.ids",
    "\n".join(
        [
            "# a trimmed copy, enough for the fixture",
            "8086  Intel Corporation",
            "\t1533  I210 Gigabit Network Connection",
            "\t15fb  Ethernet Connection (7) I219-LM",
            "# 125b and 125c are absent on purpose: a shipped pci.ids is often",
            "# older than the silicon, and nicscope must fall back to its table.",
            "",
            "C 00  Unclassified device",
        ]
    ),
)

# ------------------------------------------------------------- commands ----
cmd("ethtool --version", "ethtool version 6.1\n")
cmd("ethtool --json -k eno1", "", rc=1, stderr="ethtool: bad command line argument(s)\n", failure="unsupported")

TIMESTAMP_FULL = """Time stamping parameters for {name}:
Capabilities:
\thardware-transmit     (SOF_TIMESTAMPING_TX_HARDWARE)
\tsoftware-transmit     (SOF_TIMESTAMPING_TX_SOFTWARE)
\thardware-receive      (SOF_TIMESTAMPING_RX_HARDWARE)
\tsoftware-receive      (SOF_TIMESTAMPING_RX_SOFTWARE)
\tsoftware-system-clock (SOF_TIMESTAMPING_SOFTWARE)
\thardware-raw-clock    (SOF_TIMESTAMPING_RAW_HARDWARE)
PTP Hardware Clock: {phc}
Hardware Transmit Timestamp Modes:
\toff                   (HWTSTAMP_TX_OFF)
\ton                    (HWTSTAMP_TX_ON)
{onestep}Hardware Receive Filter Modes:
\tnone                  (HWTSTAMP_FILTER_NONE)
\tall                   (HWTSTAMP_FILTER_ALL)
"""

TIMESTAMP_NONE = """Time stamping parameters for {name}:
Capabilities:
\tsoftware-transmit     (SOF_TIMESTAMPING_TX_SOFTWARE)
\tsoftware-receive      (SOF_TIMESTAMPING_RX_SOFTWARE)
\tsoftware-system-clock (SOF_TIMESTAMPING_SOFTWARE)
PTP Hardware Clock: none
Hardware Transmit Timestamp Modes: none
Hardware Receive Filter Modes: none
"""

for port in PORTS:
    name = port["name"]
    if port["phc"] is None:
        cmd(f"ethtool -T {name}", TIMESTAMP_NONE.format(name=name))
    else:
        onestep = "\tone-step-sync         (HWTSTAMP_TX_ONESTEP_SYNC)\n" if port["driver"] == "igc" else ""
        cmd(f"ethtool -T {name}", TIMESTAMP_FULL.format(name=name, phc=port["phc"], onestep=onestep))

    cmd(
        f"ethtool -i {name}",
        f"driver: {port['driver']}\nversion: {KERNEL}\nfirmware-version: {port['firmware']}\n"
        f"expansion-rom-version: \nbus-info: {port['bdf']}\nsupports-statistics: yes\n"
        "supports-test: yes\nsupports-eeprom-access: yes\nsupports-register-dump: yes\n"
        "supports-priv-flags: yes\n",
    )
    speed_line = f"\tSpeed: {port['speed']}Mb/s\n\tDuplex: Full\n" if port["speed"] else "\tSpeed: Unknown!\n\tDuplex: Unknown! (255)\n"
    cmd(
        f"ethtool {name}",
        f"Settings for {name}:\n"
        "\tSupported ports: [ TP ]\n"
        "\tSupported link modes:   10baseT/Half 10baseT/Full\n"
        "\t                        100baseT/Half 100baseT/Full\n"
        "\t                        1000baseT/Full\n"
        "\tSupports auto-negotiation: Yes\n"
        f"{speed_line}"
        "\tPort: Twisted Pair\n"
        "\tAuto-negotiation: on\n"
        f"\tLink detected: {'yes' if port['state'] == 'up' else 'no'}\n",
    )
    cmd(
        f"ethtool -k {name}",
        f"Features for {name}:\nrx-checksumming: on\ntx-checksumming: on\n"
        "\ttx-checksum-ipv4: on\nscatter-gather: on\ntcp-segmentation-offload: on\n"
        "rx-vlan-offload: on [fixed]\n",
    )
    cmd(f"ethtool -c {name}", f"Coalesce parameters for {name}:\nAdaptive RX: off  TX: off\nrx-usecs: 3\ntx-usecs: 0\n")
    cmd(
        f"ethtool -g {name}",
        f"Ring parameters for {name}:\nPre-set maximums:\nRX:\t\t4096\nRX Mini:\tn/a\n"
        "RX Jumbo:\tn/a\nTX:\t\t4096\nCurrent hardware settings:\nRX:\t\t512\nTX:\t\t512\n",
    )
    cmd(f"ethtool -l {name}", f"Channel parameters for {name}:\nPre-set maximums:\nCombined:\t4\nCurrent hardware settings:\nCombined:\t4\n")
    cmd(f"ethtool --show-priv-flags {name}", f"Private flags for {name}:\nlegacy-rx: off\n")
    cmd(f"ethtool -m {name}", "", rc=1, stderr="netlink error: Operation not supported\n", failure="unsupported")
    cmd(f"ethtool -P {name}", f"Permanent address: {port['mac']}\n")
    cmd(
        f"ip -d -j link show {name}",
        json.dumps(
            [
                {
                    "ifindex": 2 + PORTS.index(port),
                    "ifname": name,
                    "operstate": port["state"].upper(),
                    "mtu": 1500,
                    "address": port["mac"],
                    "link_type": "ether",
                    "parentbus": "pci",
                    "parentdev": port["bdf"],
                }
            ]
        ),
    )
    cmd(
        f"udevadm info /sys/class/net/{name}",
        f"P: /devices/pci0000:00/{name}\nE: INTERFACE={name}\n"
        f"E: ID_NET_NAME_PATH=enp{PORTS.index(port)}s0\nE: ID_NET_NAME_MAC=enx{port['mac'].replace(':', '')}\n"
        f"E: ID_NET_DRIVER={port['driver']}\nE: ID_PATH=pci-{port['bdf']}\n",
    )
    cmd(f"tc qdisc show dev {name}", "qdisc mq 0: root \nqdisc pfifo_fast 0: parent :4 bands 3\n")

# ``lspci -D -vvv``. The PTM blocks are the point of the fixture.
LSPCI = """0000:00:1c.4 PCI bridge: Intel Corporation Device 7ab4 (rev 11) (prog-if 00 [Normal decode])
\tCapabilities: [40] Express (v2) Root Port (Slot+), MSI 00
\t\tLnkCap:\tPort #5, Speed 8GT/s, Width x4, ASPM L1, Exit Latency L1 <16us
\t\tLnkSta:\tSpeed 5GT/s (downgraded), Width x1 (downgraded)
\tCapabilities: [150 v1] Precision Time Measurement
\t\tPTMCap: Requester:- Responder:+ Root:+
\t\tPTMClockGranularity: 4ns
\t\tPTMControl: Enabled:+ RootSelected:+
\tKernel driver in use: pcieport

0000:00:1c.5 PCI bridge: Intel Corporation Device 7ab5 (rev 11) (prog-if 00 [Normal decode])
\tCapabilities: [40] Express (v2) Root Port (Slot+), MSI 00
\t\tLnkCap:\tPort #6, Speed 8GT/s, Width x4, ASPM L1, Exit Latency L1 <16us
\t\tLnkSta:\tSpeed 5GT/s (downgraded), Width x1 (downgraded)
\tKernel driver in use: pcieport

0000:00:1d.0 PCI bridge: Intel Corporation Device 7ab0 (rev 11) (prog-if 00 [Normal decode])
\tCapabilities: [40] Express (v2) Root Port (Slot+), MSI 00
\t\tLnkCap:\tPort #9, Speed 8GT/s, Width x4, ASPM L1, Exit Latency L1 <16us
\t\tLnkSta:\tSpeed 2.5GT/s (downgraded), Width x1 (downgraded)
\tKernel driver in use: pcieport

0000:00:1f.6 Ethernet controller: Intel Corporation Ethernet Connection (7) I219-LM (rev 11)
\tSubsystem: Intel Corporation Device 0000
\tKernel driver in use: e1000e

0000:01:00.0 Ethernet controller: Intel Corporation Ethernet Controller I226-LM (rev 04)
\tSubsystem: Intel Corporation Device 0000
\tCapabilities: [a0] Express (v2) Endpoint, MSI 00
\t\tLnkCap:\tPort #0, Speed 5GT/s, Width x1, ASPM L1, Exit Latency L1 <4us
\t\tLnkSta:\tSpeed 5GT/s (ok), Width x1 (ok)
\tCapabilities: [1f0 v1] Precision Time Measurement
\t\tPTMCap: Requester:+ Responder:- Root:-
\t\tPTMClockGranularity: 4ns
\t\tPTMControl: Enabled:+ RootSelected:-
\tKernel driver in use: igc

0000:02:00.0 Ethernet controller: Intel Corporation Ethernet Controller I226-V (rev 04)
\tSubsystem: Intel Corporation Device 0000
\tCapabilities: [a0] Express (v2) Endpoint, MSI 00
\t\tLnkCap:\tPort #0, Speed 5GT/s, Width x1, ASPM L1, Exit Latency L1 <4us
\t\tLnkSta:\tSpeed 5GT/s (ok), Width x1 (ok)
\tCapabilities: [1f0 v1] Precision Time Measurement
\t\tPTMCap: Requester:+ Responder:- Root:-
\t\tPTMClockGranularity: 4ns
\t\tPTMControl: Enabled:- RootSelected:-
\tKernel driver in use: igc

0000:03:00.0 PCI bridge: ASMedia Technology Inc. ASM1182e PCIe Switch Port (prog-if 00 [Normal decode])
\tCapabilities: [80] Express (v2) Upstream Port, MSI 00
\t\tLnkCap:\tPort #0, Speed 5GT/s, Width x2, ASPM L1
\t\tLnkSta:\tSpeed 2.5GT/s (downgraded), Width x1 (downgraded)
\tKernel driver in use: pcieport

0000:04:00.0 Ethernet controller: Intel Corporation I210 Gigabit Network Connection (rev 03)
\tSubsystem: Intel Corporation Device 0000
\tCapabilities: [a0] Express (v2) Endpoint, MSI 00
\t\tLnkCap:\tPort #0, Speed 2.5GT/s, Width x4, ASPM L1, Exit Latency L1 <4us
\t\tLnkSta:\tSpeed 2.5GT/s (ok), Width x1 (downgraded)
\tKernel driver in use: igb
"""
cmd("lspci -D -vvv", LSPCI)

cmd(
    "dmidecode -t 41",
    "# dmidecode 3.5\nGetting SMBIOS data from sysfs.\nSMBIOS 3.3.0 present.\n\n"
    "Handle 0x0029, DMI type 41, 11 bytes\nOnboard Device\n"
    "\tReference Designation: Onboard LAN 1\n\tType: Ethernet\n\tStatus: Enabled\n"
    "\tType Instance: 1\n\tBus Address: 0000:00:1f.6\n\n"
    "Handle 0x002a, DMI type 41, 11 bytes\nOnboard Device\n"
    "\tReference Designation: Onboard LAN 2\n\tType: Ethernet\n\tStatus: Enabled\n"
    "\tType Instance: 2\n\tBus Address: 0000:01:00.0\n\n",
)

# The kernel exposes ptm_enabled for the ports that carry it.
read("/sys/bus/pci/devices/0000:00:1c.4/ptm_enabled", "1")
read("/sys/bus/pci/devices/0000:01:00.0/ptm_enabled", "1")
read("/sys/bus/pci/devices/0000:02:00.0/ptm_enabled", "0")


def main() -> None:
    payload = {
        "schema": "nicscope-capture/1",
        "meta": {
            "synthetic": True,
            "privileged": True,
            "host": HOSTNAME,
            "note": "Generated by tests/fixtures/build_synthetic.py. No such machine exists.",
        },
        "fs": fs,
        "commands": commands,
    }
    with open(OUT, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=True)
        handle.write("\n")
    print(f"wrote {OUT}: {len(fs)} reads, {len(commands)} commands")


if __name__ == "__main__":
    main()
