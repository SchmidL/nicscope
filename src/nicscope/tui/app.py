"""The nicscope application.

Two rules from section 4 shape everything here:

**Never block the event loop.** Collection runs on a worker thread. The LED
blink runs as a detached subprocess, because ``ethtool -p`` blocks for its whole
duration. A blocked loop looks like a crashed tool.

**Cache the static facts, poll only what moves.** A full collection starts about
thirty processes. Doing that once a second would load the machine that you are
trying to measure. The poll re-reads the link state from sysfs and nothing else.
"""

from __future__ import annotations

import time
from typing import Any

from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)

from ..collectors import ethtool as ethtool_collector
from ..collectors import pipeline
from ..labels import Labels
from ..model import Interface, Report
from ..util.context import Context
from .export import ExportPane
from .ports import PortsPane
from .timing import TimingPane
from .topology import TopologyPane

HELP = """[bold]nicscope[/] — inspect the network interfaces of a measurement host.

[bold]Screens[/]
  1            Ports      one row for each interface
  2            Timing     the readiness table for the selected port
  3            Topology   the PCIe path and the PTM role of each level
  4            Export     write JSON, Markdown, CSV, DOT or linuxptp drafts

[bold]Keys[/]
  up / down    move between ports
  b            blink the LED of the selected port for 10 s
  B            blink every port in turn, to label a patch panel in one pass
  l            set a free-text label on the selected port
  r            collect again from the start
  e            go to the export screen
  ?  or  F1    this help
  q            quit

[bold]How to read a value[/]
  A field that could not be collected prints [bright_black]n/a[/] with the reason. It is
  never an empty cell, because an empty cell reads as zero.
  A readiness row is [green]pass[/], [yellow]warn[/], [red]FAIL[/] or [bright_black]unkn[/].
  [bright_black]unkn[/] is not [green]pass[/]. Run under sudo to resolve most of them.

[bold]Privilege[/]
  sysfs and most of ethtool need no root. PTM, the BIOS port label, the SFP
  module page and the LED blink do. Start the tool with sudo, or pass --sudo
  to let it call the root-only commands through `sudo -n`.

[bold]This tool is read-only.[/]
  It does not start ptp4l, it does not change a link setting, and it does not
  write to a PHC. It is safe on a live measurement system.

  Press escape or ? to close.
"""


class HelpScreen(ModalScreen):
    BINDINGS = [
        Binding("escape", "dismiss", "close"),
        Binding("question_mark", "dismiss", "close"),
        Binding("q", "dismiss", "close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(HELP)


class LabelScreen(ModalScreen[str | None]):
    """Set the free-text label of one port.

    The label is stored against the permanent MAC address, so it survives a
    rename of the interface.
    """

    BINDINGS = [Binding("escape", "cancel", "cancel")]

    def __init__(self, iface: Interface) -> None:
        super().__init__()
        self.iface = iface

    def compose(self) -> ComposeResult:
        with Vertical(id="label-box"):
            yield Label(
                f"Label for [bold]{self.iface.name}[/]\n"
                f"[bright_black]stored against the permanent address {self.iface.key}, "
                f"so it survives a rename[/]"
            )
            yield Input(
                value=self.iface.labels.get("user") or "",
                placeholder="for example: GNSS PPS in",
                id="label-input",
            )
            yield Button("Save", variant="primary", id="label-save")

    def on_mount(self) -> None:
        self.query_one("#label-input", Input).focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Input.Submitted)
    @on(Button.Pressed, "#label-save")
    def _save(self) -> None:
        self.dismiss(self.query_one("#label-input", Input).value)


class NicscopeApp(App):
    CSS_PATH = "nicscope.tcss"
    TITLE = "nicscope"

    BINDINGS = [
        Binding("q", "quit", "quit"),
        Binding("question_mark", "help", "help", key_display="?"),
        Binding("f1", "help", "help", show=False),
        Binding("1", "tab('ports')", "ports"),
        Binding("2", "tab('timing')", "timing"),
        Binding("3", "tab('topology')", "topology"),
        Binding("4", "tab('export')", "export"),
        Binding("b", "blink", "blink"),
        Binding("B", "blink_all", "blink all", key_display="B"),
        Binding("l", "label", "label"),
        Binding("r", "recollect", "refresh"),
        Binding("e", "tab('export')", "export", show=False),
    ]

    def __init__(self, ctx: Context, options: Any, stored: Labels) -> None:
        super().__init__()
        self.ctx = ctx
        self.options = options
        self.stored = stored
        self.report: Report | None = None
        self.selected: str | None = None
        self._blink_until: float = 0.0
        self._blink_queue: list[str] = []

    # -- layout -----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="ports"):
            with TabPane("Ports", id="ports"):
                yield PortsPane()
            with TabPane("Timing", id="timing"):
                yield TimingPane()
            with TabPane("Topology", id="topology"):
                yield TopologyPane()
            with TabPane("Export", id="export"):
                yield ExportPane()
        yield Static("Starting.", id="status")
        yield Footer()

    def on_mount(self) -> None:
        self.status("Collecting. Press ? for help.", busy=True)
        self.collect_worker()
        # Poll only the link state, at about 1 Hz. Section 4.
        self.set_interval(1.0, self.poll_worker)
        self.set_interval(0.25, self._tick_blink)

    # -- collection -------------------------------------------------------
    @work(thread=True, exclusive=True, group="collect")
    def collect_worker(self) -> None:
        started = time.monotonic()
        report = pipeline.collect(
            self.ctx,
            plan_speed_mbps=getattr(self.options, "plan_speed", None),
            include_virtual=getattr(self.options, "include_virtual", False),
            jobs=max(1, getattr(self.options, "jobs", 8)),
            labels=self.stored,
            progress=lambda what, done, total: self.call_from_thread(
                self.status, f"Collecting {what} ({done}/{total})", True
            ),
        )
        elapsed = time.monotonic() - started
        self.call_from_thread(self._apply, report, elapsed)

    @work(thread=True, exclusive=True, group="poll")
    def poll_worker(self) -> None:
        if self.report is None:
            return
        try:
            pipeline.refresh(self.ctx, self.report)
        except Exception:  # noqa: BLE001 - a poll must never kill the interface
            return
        self.call_from_thread(self._redraw_ports)

    def _widget(self, selector, expect=None):
        """Find a widget, or ``None`` when it is not mounted.

        A worker thread finishes on its own schedule, and a queued message can
        arrive while the tool is shutting down. Neither may raise: an exception
        from a handler prints a traceback over the terminal that the operator
        is still looking at.
        """
        try:
            return self.query_one(selector, expect) if expect else self.query_one(selector)
        except NoMatches:
            return None

    def _apply(self, report: Report, elapsed: float) -> None:
        self.report = report
        if self.selected is None and report.interfaces:
            self.selected = report.interfaces[0].name

        ports = self._widget(PortsPane)
        if ports is None:
            return
        ports.show(report, self.selected)
        ports.detail(self._current())
        for pane, argument in (
            (self._widget(TimingPane), self._current()),
            (self._widget(TopologyPane), report),
            (self._widget(ExportPane), report),
        ):
            if pane is not None:
                pane.show(argument)

        # The ports table is where every key except the export screen applies,
        # so it takes the focus as soon as there is something to move through.
        table = self._widget("#ports-table", DataTable)
        if table is not None and report.interfaces and not table.has_focus:
            table.focus()

        counts = self._counts(report)
        note = "" if report.host.privileged else "  [yellow]unprivileged: some fields are unknown[/]"
        self.status(
            f"{len(report.interfaces)} port(s) in {elapsed:.1f} s   {counts}{note}   ? help",
        )

    def _redraw_ports(self) -> None:
        ports = self._widget(PortsPane)
        if self.report is None or ports is None:
            return
        ports.show(self.report, self.selected)
        ports.detail(self._current())

    def _counts(self, report: Report) -> str:
        tally: dict[str, int] = {}
        for iface in report.interfaces:
            tally[iface.verdict] = tally.get(iface.verdict, 0) + 1
        order = ("pass", "warn", "fail", "unknown")
        colour = {"pass": "green", "warn": "yellow", "fail": "red", "unknown": "bright_black"}
        return "  ".join(f"[{colour[k]}]{tally[k]} {k}[/]" for k in order if k in tally)

    def _current(self) -> Interface | None:
        if self.report is None or self.selected is None:
            return None
        return self.report.interface(self.selected)

    # -- events -----------------------------------------------------------
    @on(DataTable.RowHighlighted, "#ports-table")
    def _row_changed(self, event: DataTable.RowHighlighted) -> None:
        if event.row_key is None or event.row_key.value is None:
            return
        self.selected = str(event.row_key.value)
        for pane in (self._widget(PortsPane), self._widget(TimingPane)):
            if pane is None:
                continue
            if isinstance(pane, PortsPane):
                pane.detail(self._current())
            else:
                pane.show(self._current())

    # -- actions ----------------------------------------------------------
    def action_tab(self, name: str) -> None:
        tabs = self._widget(TabbedContent)
        if tabs is None:
            return
        tabs.active = name
        if name == "timing":
            self.query_one(TimingPane).show(self._current())
        if name == "ports":
            self.query_one("#ports-table", DataTable).focus()
        elif name == "export":
            # Focus the format picker, never the path field. An Input swallows
            # every printable key, and that would break 1, 2, 3, 4 and q while
            # the export screen is open.
            self.query_one("#export-format").focus()

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_recollect(self) -> None:
        self.status("Collecting again.", busy=True)
        self.collect_worker()

    def action_label(self) -> None:
        iface = self._current()
        if iface is None:
            return

        def store(text: str | None) -> None:
            if text is None:
                return
            self.stored.set(iface.key, text, name=iface.name)
            path = self.stored.save()
            iface.labels["user"] = text.strip() or None
            self._redraw_ports()
            topology = self._widget(TopologyPane)
            if topology is not None and self.report is not None:
                topology.show(self.report)
            self.status(f"Label for {iface.name} written to {path}")

        self.push_screen(LabelScreen(iface), store)

    # -- the LED blink ----------------------------------------------------
    def action_blink(self) -> None:
        iface = self._current()
        if iface is not None:
            self._blink_queue = [iface.name]
            self._start_next_blink()

    def action_blink_all(self) -> None:
        if self.report is None:
            return
        self._blink_queue = [i.name for i in self.report.interfaces]
        self._start_next_blink()

    def _start_next_blink(self, seconds: int = 10) -> None:
        if not self._blink_queue:
            return
        name = self._blink_queue[0]
        handle = ethtool_collector.blink(self.ctx, name, seconds)
        if handle is None:
            self._blink_queue.clear()
            self.status("Cannot blink: ethtool is missing, or the run has no CAP_NET_ADMIN.", busy=True)
            return
        self._blink_until = time.monotonic() + seconds

    def _tick_blink(self) -> None:
        if not self._blink_queue:
            return
        left = self._blink_until - time.monotonic()
        if left > 0:
            name = self._blink_queue[0]
            queued = len(self._blink_queue) - 1
            more = f"   {queued} port(s) to go" if queued else ""
            self.status(f"Blinking {name}. {left:.0f} s left.{more}", blink=True)
            return
        self._blink_queue.pop(0)
        if self._blink_queue:
            self._start_next_blink()
        else:
            self.status("Blink finished.")

    # -- status bar -------------------------------------------------------
    def status(self, text: str, busy: bool = False, blink: bool = False) -> None:
        bar = self._widget("#status", Static)
        if bar is None:
            return
        bar.set_class(busy, "busy")
        bar.set_class(blink, "blink")
        bar.update(text)


def run(ctx: Context, options: Any, stored: Labels) -> int:
    """Entry point used by the command line."""
    NicscopeApp(ctx, options, stored).run()
    return 0
