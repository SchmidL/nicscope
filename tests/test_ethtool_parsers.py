"""Parser tests.

The text output of ``ethtool`` is not a stable interface, so each parser gets
both shapes that have been seen in the wild, plus the failure cases.
"""

from __future__ import annotations

from nicscope.collectors.ethtool import (
    _parse_features,
    _parse_sectioned,
    _parse_settings,
    _parse_timestamping_json,
    _parse_timestamping_text,
)

# A card with a PHC. Note the suffixes in brackets, which older builds print.
WITH_PHC = """Time stamping parameters for enp1s0:
Capabilities:
\thardware-transmit     (SOF_TIMESTAMPING_TX_HARDWARE)
\tsoftware-transmit     (SOF_TIMESTAMPING_TX_SOFTWARE)
\thardware-receive      (SOF_TIMESTAMPING_RX_HARDWARE)
\tsoftware-receive      (SOF_TIMESTAMPING_RX_SOFTWARE)
\tsoftware-system-clock (SOF_TIMESTAMPING_SOFTWARE)
\thardware-raw-clock    (SOF_TIMESTAMPING_RAW_HARDWARE)
PTP Hardware Clock: 0
Hardware Transmit Timestamp Modes:
\toff                   (HWTSTAMP_TX_OFF)
\ton                    (HWTSTAMP_TX_ON)
\tone-step-sync         (HWTSTAMP_TX_ONESTEP_SYNC)
Hardware Receive Filter Modes:
\tnone                  (HWTSTAMP_FILTER_NONE)
\tall                   (HWTSTAMP_FILTER_ALL)
"""

# A virtual NIC. The lists collapse onto the heading line.
WITHOUT_PHC = """Time stamping parameters for eth0:
Capabilities:
\tsoftware-transmit
\tsoftware-receive
\tsoftware-system-clock
PTP Hardware Clock: none
Hardware Transmit Timestamp Modes: none
Hardware Receive Filter Modes: none
"""

# A card that filters only PTPv2 events in hardware.
PTPV2_ONLY = """Time stamping parameters for enp5s0:
Capabilities:
\thardware-transmit
PTP Hardware Clock: 3
Hardware Transmit Timestamp Modes:
\toff
\ton
Hardware Receive Filter Modes:
\tnone
\tptpv2-event
"""


def test_timestamping_with_phc():
    parsed = _parse_timestamping_text(WITH_PHC)
    assert parsed["phc_index"] == 0
    assert parsed["tx_types"] == ["off", "on", "one-step-sync"]
    assert parsed["rx_filters"] == ["none", "all"]
    assert "hardware-transmit" in parsed["hw_capabilities"]
    assert "software-transmit" in parsed["sw_capabilities"]


def test_timestamping_without_phc_is_a_definite_no():
    """`none` is an answer. It must not read the same as `no answer`."""
    parsed = _parse_timestamping_text(WITHOUT_PHC)
    assert parsed["phc_index"] == -1
    assert parsed["tx_types"] == []
    assert parsed["rx_filters"] == []


def test_timestamping_ptpv2_only():
    parsed = _parse_timestamping_text(PTPV2_ONLY)
    assert parsed["phc_index"] == 3
    assert parsed["rx_filters"] == ["none", "ptpv2-event"]


def test_timestamping_survives_rubbish():
    for text in ("", "netlink error: Operation not permitted\n", "garbage\n\n\t\n"):
        parsed = _parse_timestamping_text(text)
        assert parsed["phc_index"] is None
        assert parsed["tx_types"] == []


def test_timestamping_json_shapes():
    """Several key spellings have shipped. Any of them must parse."""
    hyphen = {"ifname": "a", "phc-index": 2, "tx-types": ["off", "on"], "rx-filters": ["all"]}
    assert _parse_timestamping_json(hyphen)["phc_index"] == 2

    as_dict = {"phc-index": 1, "tx-types": {"off": True, "on": True, "one-step-sync": False}, "rx-filters": {"all": True}}
    parsed = _parse_timestamping_json(as_dict)
    assert parsed["tx_types"] == ["off", "on"]
    assert parsed["rx_filters"] == ["all"]


def test_timestamping_json_rejects_a_shape_it_does_not_know():
    """A rejected payload makes the caller fall back to the text parser."""
    assert _parse_timestamping_json({"something": "else"}) is None
    assert _parse_timestamping_json({"tx-types": None, "rx-filters": None}) is None


SETTINGS = """netlink error: Operation not permitted
Settings for enp1s0:
\tSupported ports: [ TP ]
\tSupported link modes:   10baseT/Half 10baseT/Full
\t                        100baseT/Half 100baseT/Full
\t                        1000baseT/Full
\tSupports auto-negotiation: Yes
\tSpeed: 1000Mb/s
\tDuplex: Full
\tPort: Twisted Pair
\tAuto-negotiation: on
\tLink detected: yes
"""


def test_settings_reads_continuation_lines():
    parsed = _parse_settings(SETTINGS)
    assert parsed["speed_mbps"] == 1000
    assert parsed["duplex"] == "Full"
    assert parsed["port"] == "Twisted Pair"
    assert parsed["autoneg"] == "on"
    assert parsed["link_detected"] is True
    assert "1000baseT/Full" in parsed["supported_modes"]
    assert "100baseT/Half" in parsed["supported_modes"]


def test_settings_of_a_down_port():
    parsed = _parse_settings("Settings for x:\n\tSpeed: Unknown!\n\tDuplex: Unknown! (255)\n")
    assert parsed["speed_mbps"] is None


FEATURES = """Features for enp1s0:
rx-checksumming: on
tx-checksumming: on
\ttx-checksum-ipv4: on
\ttx-checksum-ip-generic: off [fixed]
scatter-gather: on
rx-vlan-offload: on [fixed]
"""


def test_features():
    parsed = _parse_features(FEATURES)
    assert parsed["rx-checksumming"] == {"value": True, "fixed": False}
    assert parsed["tx-checksum-ip-generic"] == {"value": False, "fixed": True}
    assert parsed["rx-vlan-offload"] == {"value": True, "fixed": True}


RINGS = """Ring parameters for enp1s0:
Pre-set maximums:
RX:\t\t4096
RX Mini:\tn/a
TX:\t\t4096
Current hardware settings:
RX:\t\t512
TX:\t\t512
"""


def test_rings_split_by_section():
    parsed = _parse_sectioned(RINGS)
    assert parsed["max"]["RX"] == 4096
    assert parsed["current"]["RX"] == 512
    assert parsed["max"]["RX Mini"] is None


def test_coalesce_two_values_on_one_line():
    parsed = _parse_sectioned("Coalesce parameters for x:\nAdaptive RX: off  TX: off\nrx-usecs: 3\n")
    assert parsed["current"]["Adaptive RX"] == "off"
    assert parsed["current"]["TX"] == "off"
    assert parsed["current"]["rx-usecs"] == 3
