"""End-to-end collection against the synthetic machine.

Every assertion here is a statement about a port that the fixture describes.
See ``tests/fixtures/build_synthetic.py`` for what each port is meant to be.
"""

from __future__ import annotations

from nicscope.collectors import collect
from nicscope.util.context import make_context

from .conftest import SYNTHETIC


def verdicts(iface) -> dict[str, str]:
    return {c.check: c.result for c in iface.readiness}


def test_finds_the_physical_ports_and_leaves_out_the_loopback(report):
    assert [i.name for i in report.interfaces] == ["eno1", "enp1s0", "enp2s0", "enp3s0"]


def test_host_facts(report):
    assert report.host.hostname == "meas01"
    assert report.host.product == "Nuvo-7000"
    assert report.host.os == "Ubuntu 24.04.4 LTS"
    assert report.host.ethtool_version == "6.1"


def test_ports_are_ordered_by_pci_address(report):
    addresses = [i.pci.bdf for i in report.interfaces]
    assert addresses == sorted(addresses)


# ---------------------------------------------------------- enp1s0: good --
def test_the_good_port_passes_every_timing_check(report):
    iface = report.interface("enp1s0")
    result = verdicts(iface)
    for check in (
        "phc_present",
        "hw_tx_timestamp",
        "hw_rx_filter",
        "one_step",
        "pps_input",
        "pps_output",
        "ptm_endpoint",
        "ptm_chain",
        "ptm_enabled",
        "cross_timestamp",
        "pcie_link",
    ):
        assert result[check] == "pass", f"{check} is {result[check]}"


def test_the_good_port_resolves_its_pins(report):
    stamp = report.interface("enp1s0").timestamping
    assert stamp.n_pins == 4
    assert stamp.n_ext_ts == 1
    assert stamp.n_per_out == 2
    functions = {p.name: p.func_name for p in stamp.pins}
    assert functions["SDP0"] == "external timestamp"
    assert functions["SDP1"] == "periodic output"
    assert functions["SDP2"] == "none"


def test_the_good_port_prefers_the_stable_device_path(report):
    stamp = report.interface("enp1s0").timestamping
    assert stamp.phc_device == "/dev/ptp0"
    assert stamp.phc_device_stable == "/dev/ptp_grandmaster"


def test_the_good_port_proves_the_cross_timestamp(report):
    stamp = report.interface("enp1s0").timestamping
    assert stamp.cross_timestamp == "precise"
    assert stamp.precise_offset_ns == -1372


def test_the_bios_label_reaches_the_port(report):
    assert report.interface("enp1s0").labels["bios"] == "Onboard LAN 2"
    assert report.interface("eno1").labels["bios"] == "Onboard LAN 1"


def test_a_device_missing_from_pci_ids_falls_back_to_the_table(report):
    """A shipped pci.ids is often older than the silicon in front of you."""
    known = report.interface("enp3s0").pci
    assert known.device == "I210 Gigabit Network Connection"  # pci.ids knew it

    unknown = report.interface("enp1s0").pci
    assert unknown.vendor == "Intel Corporation"
    assert "I226-LM" in unknown.device
    assert "nicscope table" in unknown.device  # the source stays visible


# ------------------------------------------------- enp2s0: broken chain --
def test_the_broken_chain_is_found_and_named(report):
    iface = report.interface("enp2s0")
    result = verdicts(iface)
    assert result["ptm_endpoint"] == "pass"  # the card itself is fine
    assert result["ptm_chain"] == "warn"  # the path above it is not
    assert iface.ptm.chain_ok is False

    detail = next(c.detail for c in iface.readiness if c.check == "ptm_chain")
    assert "0000:00:1c.5" in detail


def test_a_bridge_without_the_capability_is_absent_not_unknown(report):
    """Read the whole configuration space, find no PTM: that is a definite no."""
    chain = report.interface("enp2s0").ptm.chain
    root_port = chain[0]
    assert root_port.present is False
    assert root_port.responder is False


def test_a_claimed_but_dead_cross_timestamp_is_a_warning(report):
    iface = report.interface("enp2s0")
    assert iface.timestamping.cross_timestamp == "claimed"
    assert verdicts(iface)["cross_timestamp"] == "warn"


def test_a_down_port_reports_no_speed(report):
    iface = report.interface("enp2s0")
    assert iface.link.state == "down"
    assert iface.link.speed_mbps is None  # sysfs says -1, which is not a speed
    assert verdicts(iface)["link_speed"] == "warn"


# ------------------------------------------------- enp3s0: no PTM at all --
def test_the_switch_path_has_three_levels(report):
    chain = report.interface("enp3s0").ptm.chain
    assert [n.bdf for n in chain] == ["0000:00:1d.0", "0000:03:00.0", "0000:04:00.0"]
    assert [n.kind for n in chain] == ["root_port", "bridge", "endpoint"]


def test_a_narrow_pcie_link_is_a_warning(report):
    iface = report.interface("enp3s0")
    assert iface.pci.link.degraded is True
    assert verdicts(iface)["pcie_link"] == "warn"


def test_two_step_only_is_a_warning_not_a_failure(report):
    assert verdicts(report.interface("enp3s0"))["one_step"] == "warn"


# ----------------------------------------------------- eno1: no PHC -----
def test_a_port_without_a_phc_fails_the_three_required_rows(report):
    result = verdicts(report.interface("eno1"))
    assert result["phc_present"] == "fail"
    assert result["hw_tx_timestamp"] == "fail"
    assert result["hw_rx_filter"] == "fail"
    assert report.interface("eno1").verdict == "fail"


def test_a_port_without_a_phc_implies_no_linuxptp_call(report):
    assert report.interface("eno1").commands == {}


# ------------------------------------------------------------- general --
def test_no_collector_raised(report):
    """Whatever a command printed, the collection must have finished."""
    assert report.errors == []
    for iface in report.interfaces:
        for problem in iface.errors:
            assert problem.reason, f"{iface.name}: an error without a reason"
            assert problem.kind in ("missing_tool", "permission", "unsupported", "parse", "failed")


def test_restricting_to_one_interface(report):
    ctx = make_context(replay=SYNTHETIC, only=["enp1s0"])
    single = collect(ctx, jobs=1)
    assert [i.name for i in single.interfaces] == ["enp1s0"]


def test_threads_give_the_same_answer_as_one_worker(payload):
    """The workers must not race for the shared cache."""
    ctx = make_context(replay=SYNTHETIC)
    threaded = collect(ctx, plan_speed_mbps=1000, jobs=8).to_dict()
    threaded.pop("collected_at")
    single = dict(payload)
    single.pop("collected_at")
    assert threaded == single
