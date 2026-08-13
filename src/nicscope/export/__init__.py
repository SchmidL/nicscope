"""Export formats. All of them are derived from the canonical JSON document."""

from __future__ import annotations

from ..model import Report
from . import csv, dot, json, linuxptp, markdown

FORMATS = {
    "json": (json.render, ".json"),
    "markdown": (markdown.render, ".md"),
    "md": (markdown.render, ".md"),
    "dot": (dot.render, ".dot"),
    "csv": (csv.render, ".csv"),
    "linuxptp": (linuxptp.render, ".conf"),
}


def render(report: Report, fmt: str) -> str:
    """Render a report in one format. Raises ``KeyError`` on an unknown name."""
    return FORMATS[fmt][0](report)


def extension(fmt: str) -> str:
    return FORMATS[fmt][1]


__all__ = ["FORMATS", "csv", "dot", "extension", "json", "linuxptp", "markdown", "render"]
