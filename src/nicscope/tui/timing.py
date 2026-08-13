"""The timing screen: the readiness table for the selected port.

Section 4 of the specification. Below the table it shows the raw ``ethtool -T``
block, the PHC pin table and the linuxptp calls that this port implies.

The raw block is there on purpose. A verdict that a person cannot check against
the source is a verdict that a person cannot trust.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ..model import Interface
from . import format as fmt


class TimingPane(Vertical):
    def compose(self):
        yield Static("", id="timing-head")
        yield DataTable(id="timing-table", cursor_type="row", zebra_stripes=True)
        yield Static("", id="timing-detail")

    def on_mount(self) -> None:
        table = self.query_one("#timing-table", DataTable)
        table.add_column("RESULT", width=7, key="result")
        table.add_column("CHECK", width=18, key="check")
        table.add_column("DETAIL", key="detail")

    def show(self, iface: Interface | None) -> None:
        head = self.query_one("#timing-head", Static)
        table = self.query_one("#timing-table", DataTable)
        detail = self.query_one("#timing-detail", Static)

        table.clear()
        if iface is None:
            head.update("No interface selected.")
            detail.update("")
            return

        head.update(f"[bold]{fmt.describe(iface)}[/]   verdict {fmt.result(iface.verdict)}")
        for check in iface.readiness:
            table.add_row(fmt.result(check.result), check.check, check.detail or "-")
        detail.update(_detail(iface))


def _detail(iface: Interface) -> str:
    stamp = iface.timestamping
    lines: list[str] = []

    if iface.commands:
        lines.append("[bold]Implied linuxptp calls[/]  [bright_black](drafts, not tuned)[/]")
        for value in iface.commands.values():
            lines.append(f"  {value}")
        lines.append("")

    lines.append("[bold]PHC pins[/]")
    if stamp.pins:
        lines.append("  [bright_black]index  name    function            channel  source[/]")
        for pin in stamp.pins:
            function = pin.func_name or (str(pin.func) if pin.func is not None else None)
            lines.append(
                f"  {pin.index:<6} {(pin.name or '?'):<7} "
                f"{fmt.value(function, 'needs root'):<19} "
                f"{fmt.value(pin.chan, 'needs root'):<8} {pin.source}"
            )
    elif stamp.phc_index is not None and stamp.phc_index >= 0:
        lines.append("  [bright_black]none. This clock has no programmable pin.[/]")
    else:
        lines.append("  [bright_black]no PHC on this port.[/]")
    lines.append("")

    lines.append("[bold]Cross timestamp[/]")
    lines.append(
        f"  {fmt.value(stamp.cross_timestamp, 'the PHC device could not be opened')}"
        + (f"   offset {stamp.precise_offset_ns} ns" if stamp.precise_offset_ns is not None else "")
    )
    lines.append(
        "  [bright_black]precise: PTP_SYS_OFFSET_PRECISE works, so the PHC-to-system offset "
        "has no read-latency error.[/]"
    )
    lines.append(
        "  [bright_black]extended: the kernel brackets the read and leaves a residual of "
        "some hundred nanoseconds.[/]"
    )
    lines.append("")

    lines.append("[bold]PCIe path and PTM[/]")
    if iface.ptm.chain:
        lines.append("  [bright_black]address        role         req  resp  root  on    gran[/]")
        for node in iface.ptm.chain:
            lines.append(
                f"  {node.bdf:<14} {node.kind.replace('_', ' '):<12} "
                f"{_flag(node.requester)}    {_flag(node.responder)}     "
                f"{_flag(node.root)}     {_flag(node.enabled)}   "
                f"{(str(node.granularity_ns) + ' ns') if node.granularity_ns else '-'}"
            )
    else:
        lines.append(f"  {fmt.unknown(fmt.why_unknown(iface, 'lspci') or 'no PCIe path')}")
    lines.append("")

    lines.append("[bold]ethtool -T[/]  [bright_black](raw)[/]")
    if stamp.raw:
        for line in stamp.raw.splitlines():
            lines.append(f"  [bright_black]{_escape(line)}[/]")
    else:
        lines.append(f"  {fmt.unknown(fmt.why_unknown(iface, 'ethtool -T') or 'no output')}")
    return "\n".join(lines)


def _flag(value) -> str:
    if value is None:
        return "[bright_black]?[/]"
    return "[green]+[/]" if value else "[yellow]-[/]"


def _escape(text: str) -> str:
    """Keep Rich from reading a bracket in command output as markup."""
    return text.replace("[", r"\[")
