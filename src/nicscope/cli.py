"""The command line. Headless mode, export, diff, labels, and the TUI launcher.

The headless mode exists for two reasons. It runs the same collectors over SSH
on a machine with no terminal capability, and it makes the collectors testable
without a terminal in the way.

    nicscope                          # the interface
    nicscope --json > munin.json      # the canonical document
    nicscope --check                  # the readiness table, and an exit code
    nicscope --diff last.json         # what changed since the last campaign
"""

from __future__ import annotations

import argparse
import os
import sys

from . import __version__, export
from . import diff as diffmod
from .collectors import collect as run_collect
from .labels import Labels
from .model import Report
from .util.context import make_context

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

# Result to colour. Kept small on purpose: a measurement host often has a
# terminal with no theme at all.
COLOURS = {"pass": "\033[32m", "warn": "\033[33m", "fail": "\033[31m", "unknown": "\033[90m"}
RESET = "\033[0m"
BOLD = "\033[1m"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nicscope",
        description="Inspect the network interfaces of a Linux measurement host: "
        "which port is which, whether it can carry precise time, and what the "
        "PCIe topology looks like. The tool is read-only.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  nicscope                         start the interface\n"
            "  nicscope --json > munin.json     write the canonical document\n"
            "  nicscope --check --strict        fail the shell on any warning\n"
            "  nicscope --format md -o report.md\n"
            "  nicscope --diff before.json      compare against an earlier export\n"
            "  nicscope --record capture.json   record every read, for a bug report\n"
            "  nicscope --replay capture.json --check    run against a recording\n"
        ),
    )

    action = parser.add_argument_group("what to do")
    action.add_argument("--json", action="store_true", help="write the canonical JSON document to stdout")
    action.add_argument(
        "--format",
        choices=sorted(export.FORMATS),
        help="write this format instead of starting the interface",
    )
    action.add_argument("-o", "--output", metavar="FILE", help="write to a file instead of stdout")
    action.add_argument("--check", action="store_true", help="print the readiness table and set an exit code")
    action.add_argument("--diff", metavar="OLD.json", help="compare the current state against an earlier export")
    action.add_argument("--tui", action="store_true", help="start the interface even when stdout is redirected")

    scope = parser.add_argument_group("what to look at")
    scope.add_argument(
        "-i", "--iface", action="append", metavar="NAME", help="restrict to this interface, repeatable"
    )
    scope.add_argument(
        "--include-virtual", action="store_true", help="also list ports with no hardware behind them"
    )
    scope.add_argument(
        "--plan-speed",
        type=int,
        metavar="MBPS",
        help="the link rate the campaign plans to use, for the link_speed check",
    )

    privilege = parser.add_argument_group("privilege")
    privilege.add_argument(
        "--sudo", action="store_true", help="run the root-only commands through `sudo -n`"
    )
    privilege.add_argument(
        "--no-root",
        action="store_true",
        help="never try a root-only command, and mark those fields unknown",
    )
    privilege.add_argument(
        "--no-ioctl", action="store_true", help="do not open /dev/ptp<N>, so skip the cross-timestamp probe"
    )

    labels = parser.add_argument_group("labels")
    labels.add_argument(
        "--label",
        action="append",
        metavar="IFACE=TEXT",
        help="store a label for a port, keyed on its permanent MAC address",
    )
    labels.add_argument("--list-labels", action="store_true", help="print the stored labels and stop")

    capture = parser.add_argument_group("record and replay")
    capture.add_argument("--record", metavar="FILE", help="record every read into a capture file")
    capture.add_argument("--replay", metavar="FILE", help="read from a capture file instead of the machine")

    other = parser.add_argument_group("other")
    other.add_argument("--jobs", type=int, default=8, metavar="N", help="worker threads, default 8")
    other.add_argument(
        "--timeout", type=float, default=10.0, metavar="S", help="timeout for one command, default 10 s"
    )
    other.add_argument(
        "--strict", action="store_true", help="with --check, a warning or an unknown also fails"
    )
    other.add_argument("--no-colour", "--no-color", action="store_true", help="do not use ANSI colour")
    other.add_argument("--version", action="version", version=f"nicscope {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.list_labels:
        return _list_labels()

    if args.record and args.replay:
        parser.error("--record and --replay cannot be used together")

    stored = Labels.load()

    ctx = make_context(
        record=args.record,
        replay=args.replay,
        only=args.iface,
        allow_sudo=args.sudo,
        skip_root=args.no_root,
        probe_ioctl=not args.no_ioctl,
        timeout=args.timeout,
    )

    wants_terminal = _wants_terminal(args)
    if wants_terminal:
        return _run_tui(ctx, args, stored)

    report = run_collect(
        ctx,
        plan_speed_mbps=args.plan_speed,
        include_virtual=args.include_virtual,
        jobs=max(1, args.jobs),
        labels=stored,
    )

    if args.record:
        ctx.capture.meta = {
            "host": report.host.hostname,
            "collected_at": report.collected_at,
            "privileged": report.host.privileged,
            "nicscope_version": __version__,
        }
        ctx.capture.save(args.record)
        print(f"recorded {len(ctx.capture.fs)} reads and {len(ctx.capture.commands)} commands "
              f"into {args.record}", file=sys.stderr)

    if args.label:
        code = _apply_labels(args.label, report, stored)
        if code != EXIT_OK:
            return code

    if args.diff:
        return _run_diff(args.diff, report)

    if args.check:
        return _run_check(report, strict=args.strict, colour=_use_colour(args))

    return _write(report, args.format or "json", args.output)


# ------------------------------------------------------------- actions ----
def _wants_terminal(args) -> bool:
    if args.tui:
        return True
    if args.json or args.format or args.check or args.diff or args.label:
        return False
    return sys.stdout.isatty()


def _run_tui(ctx, args, stored: Labels) -> int:
    try:
        from .tui.app import run
    except ImportError:
        print(
            "The interface needs Textual, which is not installed.\n"
            "  pip install 'nicscope[tui]'\n"
            "Falling back to the readiness table.\n",
            file=sys.stderr,
        )
        report = run_collect(
            ctx,
            plan_speed_mbps=args.plan_speed,
            include_virtual=args.include_virtual,
            jobs=max(1, args.jobs),
            labels=stored,
        )
        return _run_check(report, strict=args.strict, colour=_use_colour(args))
    return run(ctx, args, stored)


def _run_diff(old_path: str, report: Report) -> int:
    try:
        old = diffmod.load(old_path)
    except (OSError, ValueError) as exc:
        print(f"cannot read {old_path}: {exc}", file=sys.stderr)
        return EXIT_USAGE
    result = diffmod.compare(old, report.to_dict())
    sys.stdout.write(diffmod.render(result))
    return EXIT_OK if result.empty else EXIT_FAIL


def _run_check(report: Report, *, strict: bool, colour: bool) -> int:
    out = sys.stdout
    host = report.host
    paint = _painter(colour)

    out.write(f"{paint(BOLD)}{host.hostname or 'unknown host'}{paint(RESET)}  ")
    out.write(f"{host.product or ''} {host.os or ''}  kernel {host.kernel or '?'}\n")
    out.write(f"collected {report.collected_at}\n")
    if not host.privileged:
        out.write(
            paint(COLOURS["warn"])
            + "unprivileged run: PTM, the BIOS label and the PHC ioctl are unknown, not absent. "
            + "run again with sudo, or use --sudo.\n"
            + paint(RESET)
        )
    out.write("\n")

    worst = "pass"
    for iface in report.interfaces:
        pci = iface.pci
        title = f"{paint(BOLD)}{iface.name}{paint(RESET)}"
        bits = [pci.bdf if pci else "no PCI", iface.driver.name or "no driver"]
        if pci and pci.device:
            bits.append(pci.device)
        label = iface.labels.get("user") or iface.labels.get("bios")
        if label:
            bits.append(f'"{label}"')
        out.write(f"{title}  {'  '.join(str(b) for b in bits)}\n")

        for check in iface.readiness:
            mark = check.result.upper().ljust(7)
            out.write(f"  {paint(COLOURS.get(check.result, ''))}{mark}{paint(RESET)}")
            out.write(f" {check.check:<18} {check.detail}\n")
            worst = _worse(worst, check.result)

        if iface.commands:
            out.write("  implied calls (drafts):\n")
            for value in iface.commands.values():
                out.write(f"    {value}\n")
        if iface.errors:
            dim = paint(COLOURS["unknown"])
            for problem in iface.errors:
                out.write(f"  {dim}. {problem.source}: {problem.reason}{paint(RESET)}\n")
        out.write("\n")

    if not report.interfaces:
        out.write("No physical interface found. Use --include-virtual to list the rest.\n")
        return EXIT_OK

    if worst == "fail":
        return EXIT_FAIL
    if strict and worst in ("warn", "unknown"):
        return EXIT_FAIL
    return EXIT_OK


def _write(report: Report, fmt: str, path: str | None) -> int:
    text = export.render(report, fmt)
    if path:
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            print(f"cannot write {path}: {exc}", file=sys.stderr)
            return EXIT_USAGE
        print(f"wrote {path}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return EXIT_OK


def _apply_labels(pairs: list[str], report: Report, stored: Labels) -> int:
    for pair in pairs:
        name, sep, text = pair.partition("=")
        if not sep:
            print(f"--label needs IFACE=TEXT, got {pair!r}", file=sys.stderr)
            return EXIT_USAGE
        iface = report.interface(name.strip())
        if iface is None:
            print(f"no interface named {name.strip()!r}", file=sys.stderr)
            return EXIT_USAGE
        stored.set(iface.key, text, name=iface.name)
        iface.labels["user"] = text.strip() or None
    path = stored.save()
    print(f"labels written to {path}", file=sys.stderr)
    return EXIT_OK


def _list_labels() -> int:
    stored = Labels.load()
    if not stored.entries:
        print(f"no labels stored in {stored.path}")
        return EXIT_OK
    print(f"{stored.path}\n")
    for key, entry in sorted(stored.entries.items()):
        name = entry.get("last_name") or "?"
        print(f"  {key}  {entry.get('label', '')}   (last seen as {name})")
    return EXIT_OK


# ------------------------------------------------------------- helpers ----
def _worse(current: str, candidate: str) -> str:
    order = {"pass": 0, "unknown": 1, "warn": 2, "fail": 3}
    return candidate if order.get(candidate, 0) > order.get(current, 0) else current


def _use_colour(args) -> bool:
    if args.no_colour or os.environ.get("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _painter(colour: bool):
    return (lambda code: code) if colour else (lambda code: "")


if __name__ == "__main__":
    raise SystemExit(main())
