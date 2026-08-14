# nicscope — NIC and Timing Inspection TUI

A terminal tool to inspect network interfaces on a Linux measurement host.
It answers three questions:

1. Which physical port is which?
2. Can this port carry precise time?
3. What does the topology look like, and how do I archive it?

---

## 1. Data sources

Read from sysfs where possible. Sysfs is stable and needs no root.
Use command output only where no sysfs equivalent exists.

### 1.1 Interface inventory

| Fact | Source | Root? |
|---|---|---|
| Interface list | `/sys/class/net/` | no |
| MAC address | `/sys/class/net/<if>/address` | no |
| Link state | `/sys/class/net/<if>/operstate`, `carrier` | no |
| Link speed | `/sys/class/net/<if>/speed`, `duplex` | no |
| MTU | `/sys/class/net/<if>/mtu` | no |
| PCI address | `readlink -f /sys/class/net/<if>/device` | no |
| NUMA node | `/sys/class/net/<if>/device/numa_node` | no |
| Vendor / device ID | `/sys/class/net/<if>/device/{vendor,device}` | no |
| Subsystem IDs | `.../subsystem_vendor`, `.../subsystem_device` | no |
| Driver name | `readlink -f /sys/class/net/<if>/device/driver` | no |
| Alt names, permaddr | `ip -d -j link show <if>` | no |
| udev name candidates | `udevadm info /sys/class/net/<if>`, keys `ID_NET_NAME_*` | no |

Resolve the numeric IDs to text names with the local PCI ID database at
`/usr/share/misc/pci.ids` or `/usr/share/hwdata/pci.ids`. Do not call an
online service.

### 1.2 Driver and firmware

```
ethtool -i <if>
```
Gives driver, driver version, firmware version, EEPROM version, bus info.
Firmware version matters for the i225/i226 family. Several errata are fixed
only in later firmware.

### 1.3 Capabilities

```
ethtool <if>                    # link modes, auto-negotiation, port type
ethtool -k <if>                 # offloads (JSON: --json on newer ethtool)
ethtool -c <if>                 # interrupt coalescing
ethtool -g <if>                 # ring buffer sizes
ethtool -l <if>                 # channel/queue count
ethtool --show-priv-flags <if>  # driver-specific flags
ethtool -m <if>                 # SFP module data, cage ports only
```

Newer `ethtool` supports `--json` for several sub-commands. Prefer it.
Fall back to a text parser when the flag is not accepted.

### 1.4 Timestamping and PHC

```
ethtool -T <if>
```
Report these fields:

- **Hardware transmit modes** — `off`, `on`, `one-step-sync`, `one-step-p2p`.
  One-step support removes a follow-up message and lowers jitter.
- **Hardware receive filters** — `all` is best. `ptpv2-event` only is enough
  for `ptp4l`, but blocks other protocols.
- **PHC index** — the number `N` in `/dev/ptp<N>`. This is the value that
  `ptp4l` and `ts2phc` need.

Then read the PHC itself:

```
/sys/class/ptp/ptp<N>/clock_name
/sys/class/ptp/ptp<N>/max_adjustment
/sys/class/ptp/ptp<N>/n_pins
/sys/class/ptp/ptp<N>/n_periodic_outputs
/sys/class/ptp/ptp<N>/n_external_timestamps
/sys/class/ptp/ptp<N>/pins/<name>
```

The pin files expose the SDP pins on Intel i210/i225/i226 cards. These carry
PPS in and PPS out. A pin file holds two numbers: function and channel.
Function `0` is none, `1` is external timestamp input, `2` is periodic output.
This tells you if the card can accept a PPS from a GNSS receiver.

For a stable device path, prefer `/dev/ptp_<name>` symlinks if your udev rules
create them. The `ptpN` numbering can change across reboots.

### 1.5 PCIe PTM

PTM is a PCIe capability. It is not visible to `ethtool`.

```
sudo lspci -vvv -s <bdf>
```
Look for the `Precision Time Measurement` capability block:

```
Capabilities: [1f0 v1] Precision Time Measurement
    PTMCap: Requester:+ Responder:- Root:-
    PTMClockGranularity: 4ns
    PTMControl: Enabled:+ RootSelected:-
```

PTM needs the whole chain. The endpoint must be a requester. Every bridge
above it must be a responder. The root port must show `Root:+`. Walk the
parent chain in sysfs:

```
/sys/bus/pci/devices/<bdf>/..            # parent bridge
```
Repeat until you reach the root complex. Check each level.

Some kernels expose `/sys/bus/pci/devices/<bdf>/ptm_enabled`. Test for the
file at runtime. If it is missing, parse `lspci` instead.

Also confirm kernel support and the driver log:

```
zgrep -i ptm /boot/config-$(uname -r)    # expect CONFIG_PCIE_PTM=y
dmesg | grep -i 'ptm\|igc\|igb\|ice'
```

PTM matters because it makes the `PTP_SYS_OFFSET_PRECISE` ioctl accurate.
That ioctl gives a cross-timestamp between the PHC and the system clock with
no read latency error. Without PTM the kernel falls back to
`PTP_SYS_OFFSET_EXTENDED`, which brackets the read and leaves a residual of
some hundreds of nanoseconds.

Probe the ioctl directly to prove the capability. The `testptp` tool from the
kernel tree (`tools/testing/selftests/ptp/testptp.c`) does this with
`testptp -d /dev/ptpN -x`. Ship a small ioctl wrapper instead of a parser if
you want a hard yes/no answer.

### 1.6 PCIe link quality

A slow or narrow link adds latency jitter to the register reads.

```
sudo lspci -vv -s <bdf> | grep -E 'LnkCap|LnkSta'
```
Compare `LnkSta` against `LnkCap`. A card negotiated below its capability is
a warning, not an error.

Sysfs alternative, no root needed:
```
/sys/bus/pci/devices/<bdf>/current_link_speed
/sys/bus/pci/devices/<bdf>/current_link_width
/sys/bus/pci/devices/<bdf>/max_link_speed
/sys/bus/pci/devices/<bdf>/max_link_width
```

### 1.7 TSN features

For a triggered measurement system, check the transmit scheduling support:

```
tc qdisc show dev <if>
```
The `igc` driver supports `etf` (launch time) and `taprio` offload. This lets
the NIC send a frame at an exact PHC time. Detect support by a dry-run
`tc qdisc replace ... etf ... offload` in a test, or read the driver name and
use a static capability table.

### 1.8 Physical identification

```
sudo dmidecode -t 41       # onboard device names assigned by the BIOS
ethtool -p <if> 10         # blink the port LED for 10 s
```
`dmidecode` type 41 maps a PCI address to a silkscreen label, for example
`Onboard LAN 1`. It only covers onboard ports, not add-in cards.

`ethtool -p` blocks for the whole duration. Run it in a worker thread or a
subprocess. Never block the TUI event loop.

---

## 2. Privileges

| Action | Requirement |
|---|---|
| sysfs reads | none |
| `ethtool -i/-k/-T/-c/-g/-l` | none |
| `ethtool -p` (blink) | `CAP_NET_ADMIN` |
| `ethtool -m` (SFP EEPROM) | `CAP_NET_ADMIN` |
| `lspci -vvv` config space | root |
| `dmidecode` | root |

Design for a two-tier run. Start unprivileged and mark the root-only fields
as `unknown`. Show a banner with the reason. Offer a re-run under `sudo`.

Alternative: grant the capability once.
```
sudo setcap cap_net_admin,cap_net_raw+ep /usr/bin/ethtool
```
This affects all users of `ethtool`. Weigh that before you do it.

---

## 3. Timing readiness check

Compute a per-interface verdict. Each item is pass, warn, or fail.

| Check | Pass condition | Why |
|---|---|---|
| PHC present | `ethtool -T` reports a PHC index >= 0 | needed by `ptp4l` |
| HW RX filter | `all` or `ptpv2-event` | software filters add jitter |
| HW TX timestamp | `on` present | required for a master |
| One-step | `one-step-sync` present | optional, lowers jitter |
| PPS input pin | `n_external_timestamps > 0` | needed by `ts2phc` |
| PPS output pin | `n_periodic_outputs > 0` | needed to drive other devices |
| PTM endpoint | `PTMCap: Requester:+` | precise cross-timestamp |
| PTM chain | every parent responder, root `Root:+` | PTM fails without the chain |
| PTM enabled | `PTMControl: Enabled:+` | kernel turned it on |
| Link speed | at or above the planned rate | |
| PCIe link | `LnkSta` equals `LnkCap` | avoids read latency jitter |
| Firmware | matches a known-good table | i225/i226 errata |
| NUMA | same node as the capture process | reduces jitter |

Do not merge these into one score. Show the list. The operator needs to know
which item failed.

A useful extra column: the exact `ptp4l` or `ts2phc` argument that this
interface implies, for example `ts2phc -c /dev/ptp0 -s generic`. Let the user
copy it.

---

## 4. TUI layout

Suggested screens.

```
┌─ nicscope ─────────────────────────────────────────────────┐
│ [1] Ports  [2] Timing  [3] Topology  [4] Export     ? help │
├────────────────────────────────────────────────────────────┤
│ IFACE     PCI       DRIVER  SPEED   LINK  PHC  PTM  LABEL  │
│ enp1s0    01:00.0   igc     1000    up    0    yes  LAN1   │
│ enp3s0    03:00.0   igc     1000    down  1    yes  -      │
│ enp4s0f0  04:00.0   ice     10000   up    2    no   -      │
├────────────────────────────────────────────────────────────┤
│ detail pane for the selected row                           │
└────────────────────────────────────────────────────────────┘
```

**Ports screen.** One row per interface. Key `b` blinks the LED with a
countdown in the status bar. Key `B` blinks all ports one after another, so
the operator can label a patch panel in one pass.

**Timing screen.** The readiness table from section 3 for the selected
interface. Show the raw `ethtool -T` block and the PHC pin table below it.

**Topology screen.** A tree of the PCIe path from the root complex down to
the NIC. Annotate each node with its PTM role.

```
[0000:00] Root Complex
└── 00:1c.4  Root Port         PTM Root:+ Responder:+  gran 4ns
    └── 01:00.0  I226-LM       PTM Requester:+ Enabled:+
        └── enp1s0  PHC 0  SDP pins 4  1000Mb/s  up
```

**Export screen.** Format picker and a target path.

### Interaction rules

- Collect in background workers. Never block the UI.
- Cache the static facts. Poll only link state and carrier, at about 1 Hz.
- Show a `?` overlay with the key map.
- Mark every unknown value clearly. Do not print an empty cell for a field
  that failed to collect. Print `n/a (needs root)`.

---

## 5. Export

Write one canonical JSON document. Derive the other formats from it.

```json
{
  "schema": "nicscope/1",
  "collected_at": "2026-08-13T09:14:22+02:00",
  "host": { "hostname": "meas01", "kernel": "6.8.0-40-generic",
            "product": "...", "bios": "..." },
  "interfaces": [
    {
      "name": "enp1s0",
      "mac": "aa:bb:cc:dd:ee:ff",
      "pci": {
        "bdf": "0000:01:00.0",
        "vendor_id": "8086", "device_id": "125c",
        "vendor": "Intel Corporation", "device": "Ethernet Controller I226-LM",
        "link": { "speed": "5.0 GT/s", "width": 1,
                  "max_speed": "5.0 GT/s", "max_width": 1 },
        "numa_node": 0,
        "path": ["0000:00:1c.4", "0000:01:00.0"]
      },
      "driver": { "name": "igc", "version": "6.8.0", "firmware": "2017:888d" },
      "link": { "state": "up", "speed_mbps": 1000, "duplex": "full", "mtu": 1500 },
      "timestamping": {
        "phc_index": 0, "phc_device": "/dev/ptp0", "clock_name": "igc-ptp",
        "tx_types": ["off", "on"],
        "rx_filters": ["none", "all"],
        "n_pins": 4, "n_ext_ts": 1, "n_per_out": 2,
        "cross_timestamp": "precise"
      },
      "ptm": { "requester": true, "responder": false, "enabled": true,
               "granularity_ns": 4, "chain_ok": true },
      "labels": { "bios": "Onboard LAN 1", "user": "GNSS PPS in" },
      "readiness": [
        { "check": "phc_present", "result": "pass" },
        { "check": "ptm_chain",   "result": "pass" }
      ]
    }
  ]
}
```

Additional exports, all generated from the JSON:

- **Markdown** — a report for the campaign logbook. One section per interface,
  plus the readiness table.
- **Graphviz DOT** — the PCIe tree. Render with `dot -Tsvg`. Colour a node
  green when PTM is enabled, yellow when capable but off, grey when absent.
- **CSV** — one row per interface, flat columns. For a spreadsheet inventory.
- **linuxptp fragments** — a draft `ptp4l.conf` and a `ts2phc.cfg` with the
  correct interface names and PHC devices filled in. Mark it as a draft. Do
  not claim it is tuned.

Add a `--diff <old.json>` mode. Before a campaign, compare against the last
export. It catches a swapped cable, a renamed interface, or a firmware change.

Let the user attach a free-text label to each port. Persist it in
`~/.config/nicscope/labels.json`, keyed by permanent MAC address. The MAC
survives a rename; the interface name does not.

---

## 6. Implementation notes

**Language.** Python with Textual gives the fastest path to a working TUI.
Rust with ratatui gives a single static binary, which is easier to copy to a
field machine. Both are fine. Pick by how you deploy.

**Structure.**

```
collectors/   sysfs.py  ethtool.py  lspci.py  ptp.py  dmi.py
model.py      dataclasses, one per section of the JSON schema
readiness.py  the rule table from section 3
export/       json.py  markdown.py  dot.py  csv.py  linuxptp.py
tui/          app.py  ports.py  timing.py  topology.py  export.py
cli.py        headless mode: nicscope --json > host.json
```

Keep a headless mode. It lets you run the same collector over SSH on a
machine with no terminal capability, and it makes the collectors testable.

**Testing.** Save the raw output of every command into a fixture directory.
Parse from fixtures in the unit tests. Hardware is not always available.

**Parsing.** `ethtool` text output is not a stable interface. Prefer
`--json` where it exists. Guard every parser with a version check and a
fallback. Never let a parse error kill the whole collection. Record the
failure in the JSON as an error field.

**Root-only fields.** Run `lspci -vvv` and `dmidecode` through a single
helper. If it fails with a permission error, set the field to `null` and add
the reason. The UI reads that reason and shows it.

---

## 7. Out of scope

This tool inspects. It does not configure.

It does not start `ptp4l`, `ts2phc`, or `phc2sys`. It does not change link
settings. It does not write to the PHC. A read-only tool is safe to run on a
live measurement system. Keep it that way.

A separate monitor for a running `ptp4l` — offset, path delay, port state via
the management interface — is a good second tool. Do not merge it into this
one.
