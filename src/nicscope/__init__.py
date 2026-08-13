"""nicscope — inspect the network interfaces of a Linux measurement host.

It answers three questions:

1. Which physical port is which?
2. Can this port carry precise time?
3. What does the topology look like, and how do I archive it?

The tool is read-only. It does not start ``ptp4l``, it does not change a link
setting, and it does not write to a PHC. It is safe to run on a live
measurement system.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
