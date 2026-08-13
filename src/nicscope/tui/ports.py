"""The ports screen: one row for each interface, and a detail pane below it.

Section 4 of the specification. The table answers "which physical port is
which". The key ``b`` blinks the selected port, and ``B`` blinks every port one
after another, so a patch panel can be labelled in one pass.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ..model import Interface, Report
from . import format as fmt

COLUMNS = (
    ("IFACE", 10),
    ("PCI", 13),
    ("DRIVER", 9),
    ("SPEED", 7),
    ("LINK", 6),
    ("PHC", 5),
    ("PTM", 6),
    ("LABEL", 18),
    ("READY", 6),
)


class PortsPane(Vertical):
    """The table plus the detail pane for the selected row."""

    def compose(self):
        table = DataTable(id="ports-table", cursor_type="row", zebra_stripes=True)
        yield table
        yield Static("Collecting.", id="ports-detail")

    def on_mount(self) -> None:
        table = self.query_one("#ports-table", DataTable)
        for title, width in COLUMNS:
            table.add_column(title, width=width, key=title)

    # -- updates ----------------------------------------------------------
    def show(self, report: Report, selected: str | None) -> None:
        table = self.query_one("#ports-table", DataTable)
        wanted = [i.name for i in report.interfaces]
        existing = [str(key.value) for key in table.rows]

        if existing != wanted:
            table.clear()
            for iface in report.interfaces:
                table.add_row(*self._row(iface), key=iface.name)
        else:
            for iface in report.interfaces:
                for (title, _), cell in zip(COLUMNS, self._row(iface), strict=False):
                    table.update_cell(iface.name, title, cell, update_width=False)

        if selected:
            self._move_to(table, selected)

    def _move_to(self, table: DataTable, name: str) -> None:
        for index, key in enumerate(table.rows):
            if str(key.value) == name:
                if table.cursor_row != index:
                    table.move_cursor(row=index)
                return

    def _row(self, iface: Interface) -> list[str]:
        pci = iface.pci
        return [
            iface.name,
            pci.bdf.removeprefix("0000:") if pci and pci.bdf else fmt.unknown(),
            iface.driver.name or fmt.unknown(),
            fmt.speed(iface),
            fmt.link_state(iface),
            fmt.phc(iface),
            fmt.ptm_cell(iface),
            fmt.label_of(iface),
            fmt.result(iface.verdict),
        ]

    def detail(self, iface: Interface | None) -> None:
        pane = self.query_one("#ports-detail", Static)
        pane.update(_detail(iface) if iface else "No interface selected.")


def _detail(iface: Interface) -> str:
    pci = iface.pci
    stamp = iface.timestamping
    lines: list[str] = [f"[bold]{fmt.describe(iface)}[/]", ""]

    lines.append(_pair("MAC", iface.mac, "permanent", iface.permaddr))
    if iface.altnames:
        lines.append(f"  alt names        {', '.join(iface.altnames)}")

    if pci:
        lines.append(
            f"  device           {fmt.value(pci.vendor)} {fmt.value(pci.device)}"
            + (f"  rev 0x{pci.revision:02x}" if pci.revision is not None else "")
        )
        link = pci.link
        pcie = f"{fmt.value(link.speed)} x{fmt.value(link.width)}"
        if link.degraded:
            pcie += f"  [yellow](the card can do {link.max_speed} x{link.max_width})[/]"
        lines.append(f"  PCIe link        {pcie}")
        lines.append(f"  NUMA node        {fmt.value(pci.numa_node, 'not reported')}")
    else:
        lines.append("  device           [bright_black]not a PCI device[/]")

    lines.append(
        f"  driver           {fmt.value(iface.driver.name)}"
        f"  version {fmt.value(iface.driver.version)}"
        f"  firmware {fmt.value(iface.driver.firmware, 'not reported')}"
    )
    lines.append(
        f"  link             {fmt.link_state(iface)}"
        f"  {fmt.speed(iface)} Mbit/s"
        f"  {fmt.value(iface.link.duplex)} duplex"
        f"  MTU {fmt.value(iface.link.mtu)}"
        f"  {fmt.value(iface.link.port)}"
    )
    lines.append(
        f"  PHC              {fmt.phc(iface)}"
        f"   {fmt.value(stamp.phc_device_stable or stamp.phc_device, 'no PHC')}"
        f"   {fmt.value(stamp.clock_name, 'no PHC')}"
    )
    lines.append(
        f"  PTM              requester {fmt.tri(iface.ptm.requester, fmt.why_unknown(iface, 'lspci') or 'needs root')}"
        f"   enabled {fmt.tri(iface.ptm.enabled, 'needs root')}"
        f"   chain {fmt.tri(iface.ptm.chain_ok, 'needs root')}"
    )
    lines.append(
        f"  transmit sched   etf {fmt.value(iface.tsn.etf_offload)}"
        f"   taprio {fmt.value(iface.tsn.taprio_offload)}"
        f"   [bright_black](source: {fmt.value(iface.tsn.source)})[/]"
    )

    bios = iface.labels.get("bios")
    user = iface.labels.get("user")
    lines.append(
        f"  labels           BIOS {fmt.value(bios, 'no DMI type 41 entry')}"
        f"   user {fmt.value(user, 'not set, press l')}"
    )

    if iface.errors:
        lines.append("")
        lines.append("[bright_black]not collected:[/]")
        for problem in iface.errors:
            lines.append(f"  [bright_black]{problem.source}: {problem.reason}[/]")
    return "\n".join(lines)


def _pair(label: str, first, second_label: str, second) -> str:
    return f"  {label:<16} {fmt.value(first)}   [bright_black]{second_label}[/] {fmt.value(second)}"
