"""The command line, driven end to end against the recording."""

from __future__ import annotations

import json

import pytest

from nicscope.cli import main

from .conftest import SYNTHETIC

BASE = ["--replay", SYNTHETIC, "--no-colour"]


def run(capsys, *args) -> tuple[int, str, str]:
    code = main([*BASE, *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_json_to_stdout(capsys):
    code, out, _ = run(capsys, "--json")
    assert code == 0
    payload = json.loads(out)
    assert payload["schema"] == "nicscope/1"
    assert payload["host"]["hostname"] == "meas01"


def test_check_fails_when_a_required_row_fails(capsys):
    """eno1 has no PHC, so the run must not report success."""
    code, out, _ = run(capsys, "--check")
    assert code == 1
    assert "FAIL" in out
    assert "phc_present" in out


def test_check_of_the_good_port_alone_passes(capsys):
    code, out, _ = run(capsys, "--check", "--iface", "enp1s0")
    assert code == 0
    assert "enp1s0" in out


def test_strict_turns_an_unknown_into_a_failure(capsys):
    """The firmware table is empty, so the firmware row is unknown."""
    code, _, _ = run(capsys, "--check", "--iface", "enp1s0", "--strict")
    assert code == 1


def test_plan_speed_is_applied(capsys):
    code, out, _ = run(capsys, "--check", "--iface", "enp1s0", "--plan-speed", "10000")
    assert code == 1
    assert "below the planned 10000" in out


@pytest.mark.parametrize("fmt", ["json", "markdown", "csv", "dot", "linuxptp"])
def test_writing_each_format_to_a_file(capsys, tmp_path, fmt):
    target = tmp_path / f"out.{fmt}"
    code, _, err = run(capsys, "--format", fmt, "-o", str(target))
    assert code == 0
    assert target.read_text().strip()
    assert str(target) in err


def test_diff_against_a_fresh_export(capsys, tmp_path):
    target = tmp_path / "before.json"
    run(capsys, "--format", "json", "-o", str(target))

    code, out, _ = run(capsys, "--diff", str(target))
    assert code == 0
    assert "No change" in out


def test_diff_reports_a_changed_export(capsys, tmp_path):
    target = tmp_path / "before.json"
    run(capsys, "--format", "json", "-o", str(target))

    payload = json.loads(target.read_text())
    next(i for i in payload["interfaces"] if i["name"] == "enp1s0")["driver"]["firmware"] = "old"
    target.write_text(json.dumps(payload))

    code, out, _ = run(capsys, "--diff", str(target))
    assert code == 1
    assert "firmware" in out


def test_a_missing_diff_file_is_a_usage_error(capsys):
    code, _, err = run(capsys, "--diff", "/no/such/file.json")
    assert code == 2
    assert "cannot read" in err


def test_record_and_replay_cannot_be_combined():
    with pytest.raises(SystemExit):
        main(["--record", "a.json", "--replay", "b.json"])


def test_labels_written_from_the_command_line(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    code, _, err = run(capsys, "--label", "enp1s0=GNSS PPS in")
    assert code == 0
    assert "labels written" in err

    stored = json.loads((tmp_path / "nicscope" / "labels.json").read_text())
    assert stored["labels"]["aa:bb:cc:00:01:00"]["label"] == "GNSS PPS in"

    code, out, _ = run(capsys, "--list-labels")
    assert "GNSS PPS in" in out


def test_a_label_for_an_unknown_port_is_a_usage_error(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    code, _, err = run(capsys, "--label", "nosuch0=x")
    assert code == 2
    assert "no interface named" in err


def test_a_label_without_an_equals_sign_is_a_usage_error(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    code, _, err = run(capsys, "--label", "enp1s0")
    assert code == 2
    assert "IFACE=TEXT" in err
