"""The export screen: a format picker and a target path.

Section 5 of the specification. Every format is derived from the same JSON
document, so what you see here and what a headless run writes are the same
bytes.
"""

from __future__ import annotations

import os

from textual.containers import Vertical
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, Static

from .. import export as exporters
from ..model import Report

CHOICES = [
    ("json", "JSON — the canonical document. Every other format comes from it."),
    ("markdown", "Markdown — a report for the campaign logbook."),
    ("csv", "CSV — one flat row for each port, for a spreadsheet inventory."),
    ("dot", "Graphviz DOT — the PCIe tree. Render with `dot -Tsvg`."),
    ("linuxptp", "linuxptp — draft ptp4l.conf and ts2phc.cfg fragments."),
]


class ExportPane(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.report: Report | None = None

    def compose(self):
        with Vertical(id="export-body"):
            yield Label("Format")
            with RadioSet(id="export-format"):
                for index, (name, description) in enumerate(CHOICES):
                    yield RadioButton(f"{name:<10} {description}", value=index == 0, id=f"fmt-{name}")
            yield Label("Target path")
            yield Input(placeholder="/tmp/host.json", id="export-path")
            yield Button("Write the file", variant="primary", id="export-go")
        yield Static("", id="export-result")

    def show(self, report: Report) -> None:
        self.report = report
        field = self.query_one("#export-path", Input)
        if not field.value:
            field.value = self._suggest(report, "json")

    def _suggest(self, report: Report, fmt: str) -> str:
        host = (report.host.hostname or "host").replace(" ", "_")
        stamp = report.collected_at[:10]
        return os.path.abspath(f"{host}_{stamp}{exporters.extension(fmt)}")

    def _selected(self) -> str:
        radio = self.query_one("#export-format", RadioSet)
        index = radio.pressed_index if radio.pressed_index >= 0 else 0
        return CHOICES[index][0]

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if self.report is None:
            return
        field = self.query_one("#export-path", Input)
        field.value = self._suggest(self.report, self._selected())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export-go":
            self.write()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.write()

    def write(self) -> None:
        result = self.query_one("#export-result", Static)
        if self.report is None:
            result.update("[yellow]The collection is not finished.[/]")
            return
        path = self.query_one("#export-path", Input).value.strip()
        if not path:
            result.update("[yellow]Give a target path.[/]")
            return
        fmt = self._selected()
        try:
            text = exporters.render(self.report, fmt)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except (OSError, KeyError) as exc:
            result.update(f"[red]Could not write {path}: {exc}[/]")
            return
        lines = [
            f"[green]Wrote {path}[/]  ({len(text)} bytes, format {fmt})",
            "",
        ]
        if fmt == "dot":
            lines.append(f"[bright_black]Render it:  dot -Tsvg {path} -o topology.svg[/]")
        if fmt == "json":
            lines.append(f"[bright_black]Compare later:  nicscope --diff {path}[/]")
        if fmt == "linuxptp":
            lines.append("[bright_black]This is a draft. Read every value before you use it.[/]")
        result.update("\n".join(lines))
