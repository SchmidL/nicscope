"""Collectors. One module for each data source.

Every collector obeys the same contract:

* it takes a ``Context`` and, usually, one ``Interface``,
* it never raises, whatever the command printed,
* a value it could not read stays ``None`` and gains an entry in
  ``Interface.errors`` that names the source and the reason.
"""

from . import dmi, ethtool, lspci, pciids, ptp, sysfs, tsn, udev
from .pipeline import collect, refresh

__all__ = ["collect", "dmi", "ethtool", "lspci", "pciids", "ptp", "refresh", "sysfs", "tsn", "udev"]
