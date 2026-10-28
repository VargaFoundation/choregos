# SPDX-License-Identifier: Apache-2.0
"""The agent's process is confined by the kernel before it starts (ADR 0094).

The runner launches the agent as its child, in the same container, after the run is assigned: the
warm pool is untouched. Between the two, `python -m choregos_runner.confine` restricts itself with
Landlock, then `exec`s the agent, which inherits a domain it can never leave:

- **writes** only under the workspace, the temporary directory (the agent's `$HOME` lives there) and
  `/dev`: the runner's code (`/app`, owned by the same uid) and its configuration are read-only to
  the agent;
- **TCP connections** (Landlock ABI 4) only to the egress proxy's port and the ports of the
  platform's direct endpoints (API, gateway, tool servers), when the run has an egress proxy: a
  `curl --noproxy` to port 443 is refused by the kernel, not by a guardrail the agent can avoid;
- a Landlock domain cannot `ptrace` a process outside it, so the agent cannot read the runner's
  memory or `/proc/<runner>/environ`.

Landlock filters ports, not hosts: which host a port reaches stays the NetworkPolicy's job, and
which public host is allowed the proxy's. UDP (DNS) is not filtered. Under gVisor the syscalls do
not exist: `best_effort` launches unconfined and says so, `enforce` refuses to launch.

This module is stdlib only: it runs in the agent's place, before anything else is imported.
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from ipaddress import ip_address, ip_network
from typing import Literal
from urllib.parse import urlsplit

Mode = Literal["off", "best_effort", "enforce"]
MODES: tuple[Mode, ...] = ("off", "best_effort", "enforce")
#: Exit code of the wrapper when `enforce` cannot be honoured: the agent never starts.
REFUSED = 126
#: The line prefix of the wrapper's refusal on stderr, read back by the runner.
PREFIX = "choregos-confinement:"

# Linux UAPI (include/uapi/linux/landlock.h); the three syscalls share their numbers on every
# architecture the runner image is built for (x86_64, aarch64).
_SYS_CREATE_RULESET = 444
_SYS_ADD_RULE = 445
_SYS_RESTRICT_SELF = 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_RULE_NET_PORT = 2
_PR_SET_NO_NEW_PRIVS = 38
_O_PATH = 0o10000000
_O_CLOEXEC = 0o2000000

_FS_WRITE_FILE = 1 << 1
_FS_REMOVE_DIR = 1 << 4
_FS_REMOVE_FILE = 1 << 5
_FS_MAKE = sum(1 << bit for bit in range(6, 13))  # char, dir, reg, sock, fifo, block, sym
_FS_REFER = 1 << 13  # ABI 2
_FS_TRUNCATE = 1 << 14  # ABI 3
_NET_CONNECT_TCP = 1 << 1  # ABI 4
#: The first ABI that filters TCP connections.
NET_ABI = 4


def write_access(abi: int) -> int:
    """Every right that changes the file system, for the ABI the kernel speaks."""
    access = _FS_WRITE_FILE | _FS_REMOVE_DIR | _FS_REMOVE_FILE | _FS_MAKE
    if abi >= 2:
        access |= _FS_REFER
    if abi >= 3:
        access |= _FS_TRUNCATE
    return access


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64), ("handled_access_net", ctypes.c_uint64)]


class _PathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


class _NetPortAttr(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("port", ctypes.c_uint64)]


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(None, use_errno=True)


def landlock_abi() -> int:
    """The Landlock ABI of the running kernel; 0 when there is none (old kernel, gVisor, LSM off)."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        version = _libc().syscall(_SYS_CREATE_RULESET, None, ctypes.c_size_t(0), _CREATE_RULESET_VERSION)
    except (OSError, AttributeError):
        return 0
    return max(int(version), 0)


@dataclass(frozen=True)
class Confinement:
    """What the agent's process is held to; serialised on the wrapper's command line."""

    mode: Mode
    write_roots: tuple[str, ...] = ()
    #: `None`: connections are not filtered (no egress proxy, nothing to close).
    connect_ports: tuple[int, ...] | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "mode": self.mode,
                "write_roots": list(self.write_roots),
                "connect_ports": None if self.connect_ports is None else list(self.connect_ports),
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, text: str) -> Confinement:
        data = json.loads(text)
        if data.get("mode") not in MODES:
            raise ValueError(f"unknown confinement mode {data.get('mode')!r}")
        ports = data.get("connect_ports")
        return cls(
            mode=data["mode"],
            write_roots=tuple(str(p) for p in data.get("write_roots") or ()),
            connect_ports=None if ports is None else tuple(int(p) for p in ports),
        )


@dataclass(frozen=True)
class Report:
    """What the kernel will hold the agent to, recorded in the run's journal."""

    mode: Mode
    abi: int
    files: bool
    network: bool
    write_roots: tuple[str, ...] = ()
    connect_ports: tuple[int, ...] | None = None
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def refused(self) -> bool:
        """`enforce` asked for something the kernel cannot do: the agent must not start."""
        return self.mode == "enforce" and bool(self.missing)

    def to_event(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "landlock_abi": self.abi,
            "files": self.files,
            "network": self.network,
            "write_roots": list(self.write_roots),
            "connect_ports": None if self.connect_ports is None else list(self.connect_ports),
            "missing": list(self.missing),
        }


def assess(confinement: Confinement, abi: int) -> Report:
    """What `confinement` becomes on a kernel speaking `abi`, without touching anything."""
    if confinement.mode == "off":
        return Report("off", abi, files=False, network=False)
    missing: list[str] = []
    files = abi >= 1
    if not files:
        missing.append("landlock")
    wants_network = confinement.connect_ports is not None
    network = wants_network and abi >= NET_ABI
    if wants_network and not network:
        missing.append(f"landlock abi {NET_ABI} (tcp connect)")
    return Report(
        confinement.mode,
        abi,
        files=files,
        network=network,
        write_roots=confinement.write_roots,
        connect_ports=confinement.connect_ports,
        missing=tuple(missing),
    )


def _no_proxy_matches(host: str, no_proxy: Sequence[str]) -> bool:
    for raw in no_proxy:
        entry = raw.strip().lower()
        if not entry:
            continue
        if entry == "*" or host == entry.lstrip("."):
            return True
        if entry.startswith(".") and host.endswith(entry):
            return True
        if "/" in entry:
            try:
                if ip_address(host) in ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
    return False


def _port(url: str) -> tuple[str, int] | None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    try:
        port = parts.port
    except ValueError:
        return None
    return parts.hostname.lower(), port or (443 if parts.scheme == "https" else 80)


def connect_ports(env: Mapping[str, str], endpoints: Iterable[str]) -> tuple[int, ...] | None:
    """The TCP ports the agent may connect to; `None` when the run has no egress proxy.

    Without a proxy, the agent's traffic goes out directly and closing ports would only break it.
    With one, the agent needs the proxy's port and the ports of the endpoints it reaches directly:
    the platform's own (`endpoints`), kept only when `NO_PROXY` sends them around the proxy —
    an endpoint the proxy carries needs no port of its own.
    """
    proxies = [env.get(name, "") for name in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy")]
    proxy_ports = {hp[1] for value in proxies if value and (hp := _port(value))}
    if not proxy_ports:
        return None
    no_proxy = (env.get("NO_PROXY") or env.get("no_proxy") or "").split(",")
    ports = set(proxy_ports)
    for url in endpoints:
        if (hp := _port(url)) and (
            hp[0] in ("localhost", "127.0.0.1", "::1") or _no_proxy_matches(hp[0], no_proxy)
        ):
            ports.add(hp[1])
    return tuple(sorted(ports))


def endpoint_urls(env: Mapping[str, str]) -> list[str]:
    """The URLs in an environment that name a platform endpoint: `*_URL`, `*_BASE`, `*_ENDPOINT`."""
    suffixes = ("_URL", "_BASE", "_ENDPOINT", "_BASE_URL", "_API_BASE")
    return [value for name, value in env.items() if name.upper().endswith(suffixes) and "://" in value]


def wrap(command: Sequence[str], confinement: Confinement, python: str = sys.executable) -> list[str]:
    """The command that confines itself, then becomes `command`."""
    if confinement.mode == "off":
        return list(command)
    return [python, "-m", "choregos_runner.confine", "--policy", confinement.to_json(), "--", *command]


def _check(result: int, what: str) -> int:
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, f"{what}: {os.strerror(err)}")
    return result


def apply(confinement: Confinement, abi: int | None = None) -> Report:
    """Restrict the calling process, for good. Raises when `enforce` cannot be honoured."""
    abi = landlock_abi() if abi is None else abi
    report = assess(confinement, abi)
    if report.refused:
        raise OSError(f"confinement refused: {', '.join(report.missing)} missing (landlock abi {abi})")
    if not report.files:
        return report
    libc = _libc()
    fs = write_access(abi)
    attr = _RulesetAttr(handled_access_fs=fs, handled_access_net=_NET_CONNECT_TCP if report.network else 0)
    size = ctypes.sizeof(_RulesetAttr) if abi >= NET_ABI else ctypes.sizeof(ctypes.c_uint64)
    ruleset = _check(
        libc.syscall(_SYS_CREATE_RULESET, ctypes.byref(attr), ctypes.c_size_t(size), 0),
        "landlock_create_ruleset",
    )
    try:
        for root in confinement.write_roots:
            try:
                fd = os.open(root, _O_PATH | _O_CLOEXEC)
            except FileNotFoundError:
                continue
            try:
                rule = _PathBeneathAttr(allowed_access=fs, parent_fd=fd)
                _check(
                    libc.syscall(_SYS_ADD_RULE, ruleset, _RULE_PATH_BENEATH, ctypes.byref(rule), 0),
                    f"landlock_add_rule {root}",
                )
            finally:
                os.close(fd)
        if report.network:
            for port in confinement.connect_ports or ():
                net = _NetPortAttr(allowed_access=_NET_CONNECT_TCP, port=port)
                _check(
                    libc.syscall(_SYS_ADD_RULE, ruleset, _RULE_NET_PORT, ctypes.byref(net), 0),
                    f"landlock_add_rule port {port}",
                )
        _check(libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0), "prctl(no_new_privs)")
        _check(libc.syscall(_SYS_RESTRICT_SELF, ruleset, 0), "landlock_restrict_self")
    finally:
        os.close(ruleset)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 4 or args[0] != "--policy" or args[2] != "--":
        print(f"{PREFIX} usage: python -m choregos_runner.confine --policy JSON -- COMMAND…", file=sys.stderr)
        return 2
    try:
        apply(Confinement.from_json(args[1]))
    except (OSError, ValueError) as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        return REFUSED
    command = args[3:]
    try:
        os.execvp(command[0], command)  # noqa: S606 - the agent replaces this process, no shell
    except OSError as exc:
        print(f"{PREFIX} cannot start {command[0]}: {exc}", file=sys.stderr)
        return 127
    return 0  # pragma: no cover - execvp does not return


if __name__ == "__main__":
    sys.exit(main())
