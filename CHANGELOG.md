# Changelog

All notable changes to nicscope are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] — 2026-08-13

First working version. It implements the whole specification.

### Added

- **Collectors** for sysfs, `ethtool`, `lspci`, `dmidecode`, `udevadm`, `ip`,
  `tc` and the local `pci.ids` database. Each one never raises. A value it
  could not read stays `null` and gains an entry in `errors` that names the
  source and the reason.
- **PTM chain analysis.** The parent chain is walked in sysfs and each level is
  read from `lspci -vvv`. The verdict names the level that breaks the chain,
  rather than only reporting that PTM does not work.
- **A read-only ioctl probe** on `/dev/ptp<N>`, written with `fcntl` and the
  standard library. `PTP_CLOCK_GETCAPS`, `PTP_PIN_GETFUNC` and
  `PTP_SYS_OFFSET_PRECISE` give a hard yes or no on the precise
  cross-timestamp. This replaces `testptp -x` from the kernel tree.
  `PTP_PIN_SETFUNC` is never called.
- **The readiness table** from section 3, with 14 rules. Each row is `pass`,
  `warn`, `fail` or `unknown`, and each row carries its reason. `unknown` is
  never reported as `pass`.
- **Implied linuxptp calls** for each port, with the correct interface name and
  the stable PHC device path.
- **A terminal interface** built on Textual: ports, timing, topology and export
  screens, an LED blink with a countdown, and free-text port labels.
- **A headless mode** with `--json`, `--check`, `--format` and `--diff`, and an
  exit code that a pre-flight script can use.
- **Five export formats**, all derived from one canonical JSON document: JSON,
  Markdown, CSV, Graphviz DOT and draft linuxptp fragments.
- **`--diff`**, which matches ports on the permanent MAC address so that a
  rename reads as a change on one port rather than as a port that disappeared.
- **`--record` and `--replay`**, which capture every file-system read and every
  command into one file. The whole tool then runs against that file with no
  hardware present.
- **Operator-maintained tables** in `src/nicscope/data/` for transmit-scheduling
  offload by driver, and for device advisories and known-good firmware.

### Notes

- The collectors, the readiness rules and every export format use the standard
  library only. Only the terminal interface needs Textual.
- The tool is read-only. It does not start `ptp4l`, it does not change a link
  setting, and it does not write to a PHC.
- One correction to the specification: the PHC pin count attribute is
  `n_programmable_pins` on a current kernel, not `n_pins`. Both names are read.
- The `devices.json` firmware table ships almost empty on purpose. An
  unverified firmware version is reported as `unknown`, never as `pass`.

[Unreleased]: https://github.com/SchmidL/nicscope/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/SchmidL/nicscope/releases/tag/v0.1.0
