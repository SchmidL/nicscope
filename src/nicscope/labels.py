"""Free-text port labels, kept between runs.

Section 5 of the specification: key the label on the permanent MAC address, not
on the interface name. A kernel update or a moved card renames a port. The
permanent address does not change, so a label that is keyed on it survives.

The file is ``~/.config/nicscope/labels.json``, or ``$XDG_CONFIG_HOME`` when
that is set.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

SCHEMA = "nicscope-labels/1"


def config_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "nicscope")


def config_path() -> str:
    return os.path.join(config_dir(), "labels.json")


class Labels:
    """A map from a permanent MAC address to a free-text label."""

    def __init__(self, entries: dict[str, dict[str, Any]] | None = None, path: str | None = None) -> None:
        self.entries: dict[str, dict[str, Any]] = entries or {}
        self.path = path or config_path()

    @classmethod
    def load(cls, path: str | None = None) -> Labels:
        target = path or config_path()
        try:
            with open(target, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError):
            return cls(path=target)
        entries = raw.get("labels", {})
        if not isinstance(entries, dict):
            entries = {}
        return cls(entries=entries, path=target)

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        payload = {"schema": SCHEMA, "labels": self.entries}
        temporary = f"{self.path}.tmp"
        with open(temporary, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, self.path)
        return self.path

    # -- access -----------------------------------------------------------
    def get(self, key: str | None) -> str | None:
        if not key:
            return None
        entry = self.entries.get(key.lower())
        return entry.get("label") if isinstance(entry, dict) else None

    def set(self, key: str, label: str, name: str | None = None) -> None:
        key = key.lower()
        if not label.strip():
            self.entries.pop(key, None)
            return
        self.entries[key] = {
            "label": label.strip(),
            "last_name": name,
            "updated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        }

    def apply(self, interfaces) -> None:
        """Attach the stored label to every interface that has one."""
        for iface in interfaces:
            iface.labels["user"] = self.get(iface.key)
