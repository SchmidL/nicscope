"""The terminal interface. Needs Textual.

The headless command line does not import this package, so a machine with no
Textual installed still runs ``nicscope --json`` and ``nicscope --check``.
"""

__all__ = ["app"]
