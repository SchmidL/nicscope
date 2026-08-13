"""The canonical JSON document. Every other format is derived from this one."""

from __future__ import annotations

import json as _json

from ..model import Report


def render(report: Report, *, indent: int = 2) -> str:
    return _json.dumps(report.to_dict(), indent=indent, sort_keys=False) + "\n"
