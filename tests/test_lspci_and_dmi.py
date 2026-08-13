"""PTM parsing, the chain rule, the PCIe link, and the BIOS port label."""

from __future__ import annotations

from nicscope.collectors.dmi import parse as parse_dmi
from nicscope.collectors.lspci import _chain_ok, _granularity, _short, _speed, parse
from nicscope.model import PtmNode

BLOCKS = """0000:00:1c.4 PCI bridge: Intel Corporation Device 7ab4 (rev 11) (prog-if 00 [Normal decode])
\tCapabilities: [40] Express (v2) Root Port (Slot+), MSI 00
\t\tLnkCap:\tPort #5, Speed 8GT/s, Width x4, ASPM L1, Exit Latency L1 <16us
\t\tLnkSta:\tSpeed 5GT/s (downgraded), Width x1 (downgraded)
\tCapabilities: [150 v1] Precision Time Measurement
\t\tPTMCap: Requester:- Responder:+ Root:+
\t\tPTMClockGranularity: 4ns
\t\tPTMControl: Enabled:+ RootSelected:+

0000:01:00.0 Ethernet controller: Intel Corporation Ethernet Controller I226-LM (rev 04)
\t\tLnkCap:\tPort #0, Speed 5GT/s, Width x1, ASPM L1, Exit Latency L1 <4us
\t\tLnkSta:\tSpeed 5GT/s (ok), Width x1 (ok)
\tCapabilities: [1f0 v1] Precision Time Measurement
\t\tPTMCap: Requester:+ Responder:- Root:-
\t\tPTMClockGranularity: Unimplemented
\t\tPTMControl: Enabled:+ RootSelected:-

0000:02:00.0 Ethernet controller: Intel Corporation I210 (rev 03)
\tCapabilities: <access denied>
"""


def test_parse_finds_every_block():
    devices = parse(BLOCKS)
    assert set(devices) == {"0000:00:1c.4", "0000:01:00.0", "0000:02:00.0"}


def test_parse_ptm_flags():
    devices = parse(BLOCKS)
    bridge = devices["0000:00:1c.4"]["ptm"]
    assert bridge == {
        "present": True,
        "requester": False,
        "responder": True,
        "root": True,
        "granularity_ns": 4,
        "enabled": True,
        "root_selected": True,
    }
    endpoint = devices["0000:01:00.0"]["ptm"]
    assert endpoint["requester"] is True
    assert endpoint["responder"] is False
    # "Unimplemented" is not a number, so the granularity stays unknown.
    assert endpoint["granularity_ns"] is None


def test_parse_link_numbers():
    devices = parse(BLOCKS)
    link = devices["0000:00:1c.4"]["link"]
    assert link == {"max_speed": "8 GT/s", "max_width": 4, "speed": "5 GT/s", "width": 1}


def test_access_denied_is_not_absent():
    """An unreadable configuration space is unknown, not `no PTM`."""
    devices = parse(BLOCKS)
    assert devices["0000:02:00.0"]["access_denied"] is True
    assert devices["0000:02:00.0"]["ptm"] is None


def test_granularity():
    assert _granularity("4ns") == 4
    assert _granularity("64 ns") == 64
    assert _granularity("Unimplemented") is None
    assert _granularity("Greater than 254ns") is None


def test_speed_is_normalised():
    assert _speed("5GT/s") == "5 GT/s"
    assert _speed("2.5 GT/s") == "2.5 GT/s"
    assert _speed("5.0 GT/s PCIe") == "5 GT/s"


def test_short_drops_the_class_and_the_programming_interface():
    assert _short("PCI bridge: Intel Corporation Device 7ab4 (rev 11) (prog-if 00 [Normal decode])") == (
        "Intel Corporation Device 7ab4 (rev 11)"
    )


# ------------------------------------------------------------ chain rule --
def node(bdf, **kwargs) -> PtmNode:
    return PtmNode(bdf=bdf, **kwargs)


def test_chain_passes_when_every_level_plays_its_part():
    chain = [
        node("0000:00:1c.4", responder=True, root=True, requester=False),
        node("0000:01:00.0", requester=True, responder=False, root=False),
    ]
    assert _chain_ok(chain) is True


def test_chain_fails_when_a_bridge_is_not_a_responder():
    """This is the case that fails silently on real hardware."""
    chain = [
        node("0000:00:1c.5", requester=False, responder=False, root=False),
        node("0000:02:00.0", requester=True, responder=False, root=False),
    ]
    assert _chain_ok(chain) is False


def test_chain_fails_when_the_endpoint_is_not_a_requester():
    chain = [
        node("0000:00:1d.0", responder=True, root=True),
        node("0000:04:00.0", requester=False, responder=False, root=False),
    ]
    assert _chain_ok(chain) is False


def test_chain_fails_when_no_level_is_the_root():
    chain = [
        node("0000:00:1c.4", responder=True, root=False),
        node("0000:01:00.0", requester=True, responder=False, root=False),
    ]
    assert _chain_ok(chain) is False


def test_chain_is_unknown_when_a_level_could_not_be_read():
    """Unknown must never collapse into pass or into fail."""
    chain = [node("0000:00:1c.4"), node("0000:01:00.0", requester=True)]
    assert _chain_ok(chain) is None
    assert _chain_ok([]) is None


def test_chain_of_an_integrated_endpoint_is_unknown_not_false():
    """A device on bus 0 has no PCI parent to carry the Root flag."""
    assert _chain_ok([node("0000:00:1f.6", requester=True)]) is None


# ------------------------------------------------------------------ DMI --
DMIDECODE = """# dmidecode 3.5
Getting SMBIOS data from sysfs.
SMBIOS 3.3.0 present.

Handle 0x0029, DMI type 41, 11 bytes
Onboard Device
\tReference Designation: Onboard LAN 1
\tType: Ethernet
\tStatus: Enabled
\tBus Address: 0000:00:1f.6

Handle 0x002a, DMI type 41, 11 bytes
Onboard Device
\tReference Designation: Onboard LAN 2
\tType: Ethernet
\tBus Address: 01:00.0
"""


def test_dmi_maps_address_to_label():
    labels = parse_dmi(DMIDECODE)
    assert labels["0000:00:1f.6"] == "Onboard LAN 1"
    # A short address gains the default domain.
    assert labels["0000:01:00.0"] == "Onboard LAN 2"


def test_dmi_of_nothing():
    assert parse_dmi("") == {}
