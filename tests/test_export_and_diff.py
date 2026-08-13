"""Exports, the diff mode, labels, and the ioctl numbers."""

from __future__ import annotations

import copy
import csv as csvmod
import io
import json as jsonmod

import pytest

from nicscope import diff as diffmod
from nicscope import export
from nicscope.labels import Labels
from nicscope.util import ptp_ioctl


# --------------------------------------------------------------- exports --
@pytest.mark.parametrize("fmt", sorted(export.FORMATS))
def test_every_format_renders(report, fmt):
    text = export.render(report, fmt)
    assert text.strip()
    assert text.endswith("\n")


def test_json_is_the_canonical_document(report):
    payload = jsonmod.loads(export.render(report, "json"))
    assert payload["schema"] == "nicscope/1"
    assert len(payload["interfaces"]) == 4
    assert payload["host"]["hostname"] == "meas01"


def test_json_keeps_an_unknown_as_null_never_as_zero(report):
    payload = jsonmod.loads(export.render(report, "json"))
    down = next(i for i in payload["interfaces"] if i["name"] == "enp2s0")
    assert down["link"]["speed_mbps"] is None


def test_markdown_never_leaves_an_empty_cell(report):
    """An empty cell reads as zero. Section 4."""
    for line in export.render(report, "markdown").splitlines():
        if line.startswith("|") and not line.startswith("|---"):
            for cell in line.strip("|").split("|"):
                assert cell.strip() != "", line


def test_markdown_carries_the_readiness_table(report):
    text = export.render(report, "markdown")
    assert "### Readiness" in text
    assert "ptm_chain" in text
    assert "0000:00:1c.5 is not a responder" in text


def test_csv_has_one_row_for_each_port(report):
    rows = list(csvmod.DictReader(io.StringIO(export.render(report, "csv"))))
    assert len(rows) == 4
    good = next(r for r in rows if r["iface"] == "enp1s0")
    assert good["phc_index"] == "0"
    assert good["ptm_chain_ok"] == "True"
    assert good["bios_label"] == "Onboard LAN 2"


def test_dot_colours_by_ptm_state(report):
    text = export.render(report, "dot")
    assert text.startswith("digraph nicscope {")
    assert text.count('"0000:01:00.0"') >= 1
    # the enabled endpoint is green, the capable-but-off one is yellow
    enabled = next(line for line in text.splitlines() if line.strip().startswith('"0000:01:00.0" ['))
    off = next(line for line in text.splitlines() if line.strip().startswith('"0000:02:00.0" ['))
    assert "#a8d5a2" in enabled
    assert "#f0d98c" in off


def test_dot_labels_are_not_broken_by_escaping(report):
    """The line break in a DOT label is a literal backslash-n. Keep it."""
    text = export.render(report, "dot")
    assert "\\n" in text
    assert "\\\\n" not in text


def test_linuxptp_marks_itself_as_a_draft(report):
    text = export.render(report, "linuxptp")
    assert "NOT TUNED" in text
    assert "[enp1s0]" in text
    assert "[eno1]" not in text  # no hardware transmit timestamping
    assert "ts2phc.pin_index        0" in text


def test_linuxptp_warns_when_only_the_numbered_device_exists(report):
    """A ptpN number can change across a reboot. Say so in the file."""
    text = export.render(report, "linuxptp")
    assert "udev rule" in text


# ------------------------------------------------------------------ diff --
def test_diff_of_a_report_against_itself_is_empty(payload):
    result = diffmod.compare(payload, payload)
    assert result.empty
    assert diffmod.render(result).strip().endswith("matches the earlier export.")


def test_a_rename_is_a_change_on_one_port_not_two_ports(payload):
    """The whole reason the diff keys on the permanent MAC address."""
    after = copy.deepcopy(payload)
    renamed = next(i for i in after["interfaces"] if i["name"] == "enp1s0")
    renamed["name"] = "enp0s31f6"

    result = diffmod.compare(payload, after)
    assert result.added == []
    assert result.removed == []
    assert [(c.field, c.old, c.new) for c in result.changed] == [
        ("name", "enp1s0", "enp0s31f6")
    ]


def test_a_firmware_change_is_reported(payload):
    after = copy.deepcopy(payload)
    next(i for i in after["interfaces"] if i["name"] == "enp1s0")["driver"]["firmware"] = "2020:9999"
    changed = diffmod.compare(payload, after).changed
    assert any(c.field == "driver.firmware" for c in changed)


def test_a_swapped_card_shows_as_one_gone_and_one_new(payload):
    after = copy.deepcopy(payload)
    after["interfaces"] = [i for i in after["interfaces"] if i["name"] != "enp3s0"]
    after["interfaces"].append(
        {"name": "enp3s0", "permaddr": "aa:bb:cc:99:99:99", "mac": "aa:bb:cc:99:99:99"}
    )
    result = diffmod.compare(payload, after)
    assert [r["name"] for r in result.removed] == ["enp3s0"]
    assert [a["name"] for a in result.added] == ["enp3s0"]
    assert "Ports that are gone" in diffmod.render(result)


def test_a_pcie_link_that_dropped_is_reported(payload):
    after = copy.deepcopy(payload)
    next(i for i in after["interfaces"] if i["name"] == "enp1s0")["pci"]["link"]["width"] = 0
    assert any(c.field == "pci.link.width" for c in diffmod.compare(payload, after).changed)


# ---------------------------------------------------------------- labels --
def test_labels_round_trip(tmp_path):
    path = str(tmp_path / "labels.json")
    store = Labels(path=path)
    store.set("aa:bb:cc:00:01:00", "GNSS PPS in", name="enp1s0")
    store.save()

    again = Labels.load(path)
    assert again.get("aa:bb:cc:00:01:00") == "GNSS PPS in"
    assert again.get("AA:BB:CC:00:01:00") == "GNSS PPS in"  # case does not matter
    assert again.get("no:such:ad:dr:es:s0") is None


def test_an_empty_label_removes_the_entry(tmp_path):
    store = Labels(path=str(tmp_path / "labels.json"))
    store.set("aa:bb:cc:00:01:00", "temporary")
    store.set("aa:bb:cc:00:01:00", "  ")
    assert store.entries == {}


def test_a_broken_label_file_does_not_stop_the_tool(tmp_path):
    path = tmp_path / "labels.json"
    path.write_text("{not json at all")
    assert Labels.load(str(path)).entries == {}


def test_labels_reach_the_report(tmp_path):
    from nicscope.collectors import collect
    from nicscope.util.context import make_context

    from .conftest import SYNTHETIC

    store = Labels(path=str(tmp_path / "labels.json"))
    store.set("aa:bb:cc:00:01:00", "GNSS PPS in", name="enp1s0")

    result = collect(make_context(replay=SYNTHETIC), jobs=1, labels=store)
    assert result.interface("enp1s0").labels["user"] == "GNSS PPS in"
    assert result.interface("enp2s0").labels["user"] is None


# ----------------------------------------------------------------- ioctl --
def test_ioctl_numbers_match_the_kernel():
    """Computed from asm-generic/ioctl.h. A wrong number talks to the wrong driver."""
    assert ptp_ioctl.PTP_CLOCK_GETCAPS == 0x80503D01
    assert ptp_ioctl.PTP_PIN_GETFUNC == 0xC0603D06
    assert ptp_ioctl.PTP_SYS_OFFSET_PRECISE == 0xC0403D08


def test_ioctl_structure_sizes():
    assert ptp_ioctl.SIZEOF_CLOCK_CAPS == 80
    assert ptp_ioctl.SIZEOF_SYS_OFFSET_PRECISE == 64
    assert ptp_ioctl.SIZEOF_PIN_DESC == 96


def test_probing_a_device_that_is_not_there_returns_a_reason():
    result = ptp_ioctl.probe("/dev/ptp_no_such_device")
    assert result["error"] == "no such device"
    assert result["cross_timestamp"] == "unknown"
