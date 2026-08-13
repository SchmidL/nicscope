"""The topology screen: the PCIe path from the root complex down to each NIC.

Section 4 of the specification. Each node carries its PTM role, because the
chain rule is what decides whether PTM works, and the chain is invisible in
every other view.
"""

from __future__ import annotations

from textual.containers import Vertical
from textual.widgets import Static, Tree

from ..model import Interface, PtmNode, Report
from . import format as fmt

LEGEND = (
    "[green]+[/] capability present   [yellow]-[/] absent   [bright_black]?[/] not readable, "
    "usually needs root       req = requester, resp = responder"
)


class TopologyPane(Vertical):
    def compose(self):
        yield Tree("PCIe", id="topology-tree")
        yield Static(LEGEND, id="topology-legend")

    def show(self, report: Report) -> None:
        tree = self.query_one("#topology-tree", Tree)
        tree.clear()
        host = report.host.hostname or "this host"
        tree.root.set_label(f"[bold]{host}[/]  root complex")
        tree.root.expand()

        placed: dict[str, object] = {}
        for iface in report.interfaces:
            chain = iface.ptm.chain
            if not chain:
                node = tree.root.add(f"{iface.name}  [bright_black]no PCIe path[/]")
                node.add_leaf(_iface_label(iface))
                continue

            parent = tree.root
            for step in chain:
                key = step.bdf
                if key in placed:
                    parent = placed[key]
                    continue
                parent = parent.add(_node_label(step))
                parent.expand()
                placed[key] = parent
            parent.add_leaf(_iface_label(iface))


def _node_label(node: PtmNode) -> str:
    role = node.kind.replace("_", " ")
    if node.present is False:
        ptm = "[bright_black]no PTM capability[/]"
    elif node.present is None:
        ptm = "[bright_black]PTM ?[/]"
    else:
        ptm = (
            f"PTM req {_flag(node.requester)} resp {_flag(node.responder)} "
            f"root {_flag(node.root)} on {_flag(node.enabled)}"
        )
        if node.granularity_ns:
            ptm += f"  gran {node.granularity_ns} ns"
    # Keep the line short enough that the PTM flags survive on a narrow
    # terminal. The flags are the reason this screen exists. The padding is
    # applied to the plain text, before it goes inside the markup tags.
    text = _trim(node.description, 34) if node.description else ""
    return f"[bold]{node.bdf}[/]  {role:<11}[bright_black]{text:<35}[/]  {ptm}"


def _trim(value: str, limit: int) -> str:
    value = value.replace("[", "(").replace("]", ")")
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _iface_label(iface: Interface) -> str:
    stamp = iface.timestamping
    parts = [f"[bold cyan]{iface.name}[/]"]
    parts.append(f"PHC {fmt.phc(iface)}")
    if stamp.n_pins:
        parts.append(f"{stamp.n_pins} pin(s)")
    parts.append(f"{fmt.speed(iface)} Mbit/s")
    parts.append(fmt.link_state(iface))
    label = iface.labels.get("user") or iface.labels.get("bios")
    if label:
        parts.append(f'[bright_black]"{label}"[/]')
    return "  ".join(parts)


def _flag(value) -> str:
    if value is None:
        return "[bright_black]?[/]"
    return "[green]+[/]" if value else "[yellow]-[/]"
