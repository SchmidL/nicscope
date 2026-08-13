"""The readiness rules.

The rule that every test here defends: **unknown is not pass**. A check that
could not run must say so, and it must say why.
"""

from __future__ import annotations

import pytest

from nicscope.model import Interface, PcieLink, PciInfo, PtmInfo, PtmNode, Timestamping
from nicscope.readiness import (
    _hw_rx,
    _hw_tx,
    _one_step,
    _pcie_link,
    _phc_present,
    _pps_input,
    _ptm_chain,
    evaluate,
    implied_commands,
)


def port(**stamp) -> Interface:
    iface = Interface(name="enp1s0")
    iface.timestamping = Timestamping(**stamp)
    return iface


# ------------------------------------------------------------------ PHC --
def test_phc_present():
    assert _phc_present(port(phc_index=0, clock_name="igc-ptp")).result == "pass"


def test_phc_absent_is_a_failure_not_an_unknown():
    assert _phc_present(port(phc_index=-1)).result == "fail"


def test_phc_unreadable_is_unknown():
    check = _phc_present(port())
    assert check.result == "unknown"
    assert "no answer" in check.detail


def test_phc_names_the_stable_symlink_when_udev_made_one():
    check = _phc_present(port(phc_index=0, phc_device_stable="/dev/ptp_grandmaster"))
    assert "/dev/ptp_grandmaster" in check.detail


# ------------------------------------------------------- timestamp modes --
def test_transmit_needs_on():
    assert _hw_tx(port(phc_index=0, tx_types=["off", "on"])).result == "pass"
    assert _hw_tx(port(phc_index=0, tx_types=["off"])).result == "fail"
    assert _hw_tx(port(phc_index=0)).result == "fail"
    assert _hw_tx(port()).result == "unknown"


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (["none", "all"], "pass"),
        (["none", "ptpv2-event"], "pass"),
        (["none", "ptpv2-l4-event"], "pass"),
        (["none"], "fail"),
        (["some-other"], "fail"),
    ],
)
def test_receive_filter(filters, expected):
    assert _hw_rx(port(phc_index=0, rx_filters=filters)).result == expected


def test_one_step_is_optional_so_missing_is_a_warning():
    assert _one_step(port(phc_index=0, tx_types=["off", "on"])).result == "warn"
    assert _one_step(port(phc_index=0, tx_types=["off", "on", "one-step-sync"])).result == "pass"


def test_pps_input_is_optional_so_missing_is_a_warning():
    assert _pps_input(port(phc_index=0, n_ext_ts=1)).result == "pass"
    assert _pps_input(port(phc_index=0, n_ext_ts=0)).result == "warn"
    assert _pps_input(port(phc_index=0)).result == "unknown"


# ------------------------------------------------------------------ PTM --
def test_ptm_chain_names_the_level_that_breaks_it():
    iface = port(phc_index=0)
    iface.ptm = PtmInfo(
        chain_ok=False,
        chain=[
            PtmNode("0000:00:1c.5", requester=False, responder=False, root=False),
            PtmNode("0000:02:00.0", requester=True, responder=False, root=False),
        ],
    )
    check = _ptm_chain(iface)
    assert check.result == "warn"
    assert "0000:00:1c.5" in check.detail  # a verdict without a name is useless


# ----------------------------------------------------------- PCIe link ----
def test_pcie_link_degraded_is_a_warning_with_both_numbers():
    iface = port()
    iface.pci = PciInfo(bdf="0000:04:00.0", link=PcieLink("2.5 GT/s", 1, "2.5 GT/s", 4))
    check = _pcie_link(iface)
    assert check.result == "warn"
    assert "x4" in check.detail


def test_pcie_link_at_capability_passes():
    iface = port()
    iface.pci = PciInfo(bdf="0000:01:00.0", link=PcieLink("5 GT/s", 1, "5 GT/s", 1))
    assert _pcie_link(iface).result == "pass"


def test_pcie_link_with_half_the_numbers_is_unknown():
    iface = port()
    iface.pci = PciInfo(bdf="0000:01:00.0", link=PcieLink("5 GT/s", None, None, None))
    assert _pcie_link(iface).result == "unknown"


# ------------------------------------------------------ implied commands --
def test_implied_commands_use_the_stable_device_path():
    iface = port(phc_index=0, phc_device="/dev/ptp0", phc_device_stable="/dev/ptp_gm", n_ext_ts=1, n_per_out=2)
    commands = implied_commands(iface)
    assert commands["ptp4l"] == "ptp4l -i enp1s0 -H -m"
    assert "/dev/ptp_gm" in commands["ts2phc"]
    assert "/dev/ptp0" not in commands["phc2sys"]


def test_no_ts2phc_without_an_external_timestamp_pin():
    commands = implied_commands(port(phc_index=0, phc_device="/dev/ptp0", n_ext_ts=0))
    assert "ts2phc" not in commands


def test_no_commands_without_a_phc():
    assert implied_commands(port(phc_index=-1)) == {}


# ----------------------------------------------------------------- whole --
def test_every_row_carries_a_reason(context):
    """A result the operator cannot act on is not worth printing."""
    iface = port(phc_index=0, tx_types=["off", "on"], rx_filters=["all"], n_ext_ts=1, n_per_out=1)
    for check in evaluate(context, iface):
        assert check.detail, f"{check.check} has no detail"
        assert check.why, f"{check.check} has no reason"
        assert check.result in ("pass", "warn", "fail", "unknown")
