"""The interface, driven headless.

These tests do not check how it looks. They check that it mounts, that the
collection reaches the widgets, that every screen renders without raising, and
that the keys do what the help overlay says they do.
"""

from __future__ import annotations

import types

import pytest

from nicscope.labels import Labels
from nicscope.util.context import make_context

from .conftest import SYNTHETIC

pytest.importorskip("textual", reason="the interface is an optional extra")

from textual.widgets import Input  # noqa: E402

from nicscope.tui.app import HelpScreen, LabelScreen, NicscopeApp  # noqa: E402
from nicscope.tui.export import ExportPane  # noqa: E402


def make_app(tmp_path) -> NicscopeApp:
    options = types.SimpleNamespace(plan_speed=1000, include_virtual=False, jobs=2)
    return NicscopeApp(
        make_context(replay=SYNTHETIC),
        options,
        Labels(path=str(tmp_path / "labels.json")),
    )


async def collected(pilot, app) -> None:
    for _ in range(80):
        await pilot.pause(0.05)
        if app.report is not None:
            return
    raise AssertionError("the collection never finished")


async def wait_for_screen(pilot, app, kind, limit: int = 40):
    """Pushing a screen is not instant. Wait for it rather than guess a delay."""
    for _ in range(limit):
        await pilot.pause(0.05)
        if isinstance(app.screen, kind):
            return app.screen
    raise AssertionError(f"{kind.__name__} never opened")


@pytest.mark.asyncio
async def test_collection_reaches_the_widgets(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await collected(pilot, app)
        assert [i.name for i in app.report.interfaces] == ["eno1", "enp1s0", "enp2s0", "enp3s0"]
        assert app.selected == "eno1"

        table = app.query_one("#ports-table")
        assert table.row_count == 4


@pytest.mark.asyncio
async def test_every_screen_renders(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await collected(pilot, app)
        for key, name in (("2", "timing"), ("3", "topology"), ("4", "export"), ("1", "ports")):
            await pilot.press(key)
            await pilot.pause(0.05)
            assert app.query_one("TabbedContent").active == name


@pytest.mark.asyncio
async def test_moving_the_cursor_changes_the_timing_screen(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await collected(pilot, app)
        await pilot.press("down")
        await pilot.pause(0.05)
        assert app.selected == "enp1s0"

        await pilot.press("2")
        await pilot.pause(0.05)
        table = app.query_one("#timing-table")
        assert table.row_count == len(app.report.interface("enp1s0").readiness)


@pytest.mark.asyncio
async def test_the_help_overlay_opens_and_closes(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await collected(pilot, app)
        await pilot.press("question_mark")
        await wait_for_screen(pilot, app, HelpScreen)

        await pilot.press("escape")
        for _ in range(40):
            await pilot.pause(0.05)
            if not isinstance(app.screen, HelpScreen):
                break
        assert not isinstance(app.screen, HelpScreen)


@pytest.mark.asyncio
async def test_the_export_screen_writes_a_file(tmp_path):
    app = make_app(tmp_path)
    target = tmp_path / "written.json"
    async with app.run_test(size=(110, 40)) as pilot:
        await collected(pilot, app)
        await pilot.press("4")
        await pilot.pause(0.05)

        app.query_one("#export-path", Input).value = str(target)
        app.query_one(ExportPane).write()
        await pilot.pause(0.05)

    assert target.read_text().startswith("{")


@pytest.mark.asyncio
async def test_a_label_set_in_the_interface_is_stored(tmp_path):
    app = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await collected(pilot, app)
        await pilot.press("down")  # enp1s0
        await pilot.pause(0.05)
        await pilot.press("l")
        modal = await wait_for_screen(pilot, app, LabelScreen)

        # Query from the modal, not from the app: a pushed screen is its own
        # DOM root, and the app still holds the default screen.
        modal.query_one("#label-input", Input).value = "GNSS PPS in"
        await pilot.press("enter")
        await pilot.pause(0.1)

        assert app.report.interface("enp1s0").labels["user"] == "GNSS PPS in"
        assert app.stored.get("aa:bb:cc:00:01:00") == "GNSS PPS in"


@pytest.mark.asyncio
async def test_the_poll_does_not_start_a_full_collection(tmp_path):
    """Section 4: cache the static facts, poll only the link state."""
    app = make_app(tmp_path)
    async with app.run_test(size=(110, 40)) as pilot:
        await collected(pilot, app)
        before = len(app.ctx.runner.log)
        app.poll_worker()
        await pilot.pause(0.3)
        # The poll reads sysfs only. It runs no command at all.
        assert len(app.ctx.runner.log) == before
