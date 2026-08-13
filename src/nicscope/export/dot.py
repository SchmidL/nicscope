"""The PCIe tree as a Graphviz document.

Render it with::

    nicscope --format dot -o topology.dot
    dot -Tsvg topology.dot -o topology.svg

Colour, as section 5 asks:

* **green**  — PTM is present and enabled,
* **yellow** — PTM is present and off,
* **grey**   — no PTM capability, or the capability could not be read.
"""

from __future__ import annotations

from ..model import PtmNode, Report

GREEN = "#a8d5a2"
YELLOW = "#f0d98c"
GREY = "#d6d6d6"
BLUE = "#bcd4ea"


def render(report: Report) -> str:
    lines: list[str] = [
        "digraph nicscope {",
        "  rankdir=LR;",
        '  graph [fontname="Helvetica", labelloc="t", '
        f'label="PCIe topology and PTM — {_escape(report.host.hostname or "unknown")} '
        f'— {_escape(report.collected_at)}"];',
        '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=10];',
        '  edge [arrowhead=none, color="#888888"];',
        "",
    ]

    seen: dict[str, PtmNode] = {}
    edges: set[tuple[str, str]] = set()
    roots: set[str] = set()

    for iface in report.interfaces:
        chain = iface.ptm.chain
        if not chain:
            continue
        for position, node in enumerate(chain):
            # Keep the record that carries the most information.
            if node.bdf not in seen or _score(node) > _score(seen[node.bdf]):
                seen[node.bdf] = node
            if position == 0:
                roots.add(node.bdf)
            else:
                edges.add((chain[position - 1].bdf, node.bdf))

    if roots:
        lines.append('  "rc" [label="Root Complex", fillcolor="#eeeeee", shape=box3d];')
        for bdf in sorted(roots):
            edges.add(("rc", bdf))

    for bdf, node in sorted(seen.items()):
        lines.append(f'  "{bdf}" [label="{_label(node)}", fillcolor="{_colour(node)}"];')

    for iface in report.interfaces:
        node_id = f"if_{iface.name}"
        lines.append(f'  "{node_id}" [label="{_iface_label(iface)}", fillcolor="{BLUE}", shape=note];')
        if iface.pci and iface.pci.bdf and iface.pci.bdf in seen:
            edges.add((iface.pci.bdf, node_id))

    lines.append("")
    for parent, child in sorted(edges):
        lines.append(f'  "{parent}" -> "{child}";')

    lines.append("")
    lines.append("  subgraph cluster_legend {")
    lines.append('    label="PTM"; fontname="Helvetica"; fontsize=10; color="#bbbbbb";')
    lines.append(f'    l_on [label="enabled", fillcolor="{GREEN}"];')
    lines.append(f'    l_off [label="capable, off", fillcolor="{YELLOW}"];')
    lines.append(f'    l_none [label="absent or unknown", fillcolor="{GREY}"];')
    lines.append("    l_on -> l_off -> l_none [style=invis];")
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _score(node: PtmNode) -> int:
    """Prefer a record with real PTM data over a placeholder."""
    return sum(1 for value in (node.requester, node.responder, node.root, node.enabled) if value is not None)


def _colour(node: PtmNode) -> str:
    if node.enabled:
        return GREEN
    if node.present and (node.requester or node.responder or node.root):
        return YELLOW
    return GREY


def _label(node: PtmNode) -> str:
    parts = [node.bdf, node.kind.replace("_", " ")]
    if node.description:
        parts.append(_trim(node.description, 40))
    flags = []
    if node.requester:
        flags.append("Requester")
    if node.responder:
        flags.append("Responder")
    if node.root:
        flags.append("Root")
    if flags:
        parts.append("PTM " + " ".join(flags))
    elif node.present is False:
        parts.append("no PTM")
    else:
        parts.append("PTM unknown")
    if node.granularity_ns:
        parts.append(f"granularity {node.granularity_ns} ns")
    if node.enabled is True:
        parts.append("enabled")
    elif node.enabled is False:
        parts.append("not enabled")
    return "\\n".join(_escape(p) for p in parts)


def _iface_label(iface) -> str:
    stamp = iface.timestamping
    parts = [iface.name]
    if stamp.phc_index is not None and stamp.phc_index >= 0:
        parts.append(f"PHC {stamp.phc_index}")
    else:
        parts.append("no PHC")
    if stamp.n_pins:
        parts.append(f"{stamp.n_pins} pin(s)")
    parts.append(f"{iface.link.speed_mbps} Mbit/s" if iface.link.speed_mbps else "no link speed")
    parts.append(iface.link.state or "unknown")
    label = iface.labels.get("user") or iface.labels.get("bios")
    if label:
        parts.append(_trim(label, 30))
    return "\\n".join(_escape(p) for p in parts)


def _trim(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _escape(value: str) -> str:
    """Make a string safe inside a quoted DOT label.

    A backslash is dropped rather than doubled. The label joiner already uses
    the literal two-character sequence ``\\n`` for a line break, and doubling a
    backslash here would turn that break into visible text.
    """
    return str(value).replace("\\", "/").replace('"', '\\"').replace("\n", " ")
