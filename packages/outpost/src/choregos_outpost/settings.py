# SPDX-License-Identifier: Apache-2.0
"""Where the daemon finds its files, and how it reaches the door.

Nothing here comes from the platform: the paths, the certificate authority and the outbound proxy
are the operator's, given on the command line or in the environment.

- **The policy file**: `--policy`, `CHOREGOS_OUTPOST_POLICY`, `/etc/choregos-outpost/policy.yaml`.
- **The state directory**: `--state-dir`, `CHOREGOS_OUTPOST_STATE_DIR`,
  `/var/lib/choregos-outpost`. It holds `identity/` (the keys) and `journal/` (the call ids), and
  must outlive a restart. `--identity-dir` moves the keys elsewhere, a volume mounted read-only for
  instance.
- **The certificate authority**: the system's trust store, to which `--ca-file`
  (`CHOREGOS_OUTPOST_CA_FILE`) adds the authority of an outbound proxy that inspects TLS, or of a
  private one. Verification is never switched off.
- **The outbound proxy**: `--proxy` (`CHOREGOS_OUTPOST_PROXY`), else the usual `HTTPS_PROXY` /
  `HTTP_PROXY` unless `NO_PROXY` covers the door. It carries the connection to the door only: the
  endpoints are in the outpost's own network and are called directly.
"""

from __future__ import annotations

import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DEFAULT_POLICY = Path("/etc/choregos-outpost/policy.yaml")
DEFAULT_STATE_DIR = Path("/var/lib/choregos-outpost")


class SettingsError(Exception):
    """A path or an address the operator gave cannot be used; the message says which."""


@dataclass(frozen=True)
class Settings:
    policy: Path = DEFAULT_POLICY
    state_dir: Path = DEFAULT_STATE_DIR
    identity: Path | None = None
    ca_file: Path | None = None
    proxy: str | None = None

    @property
    def identity_dir(self) -> Path:
        return self.identity if self.identity is not None else self.state_dir / "identity"

    @property
    def journal_dir(self) -> Path:
        return self.state_dir / "journal"

    def trust(self) -> ssl.SSLContext:
        """What the daemon verifies a certificate against: the system's store, and `ca_file` if given."""
        context = ssl.create_default_context()
        if self.ca_file is not None:
            try:
                context.load_verify_locations(cafile=str(self.ca_file))
            except (OSError, ssl.SSLError) as error:
                raise SettingsError(
                    f"{self.ca_file} is not a certificate authority file ({error})"
                ) from error
        return context

    def proxy_for(self, url: str) -> str | None:
        """The outbound proxy that carries a connection to `url`, or None for a direct one."""
        if self.proxy:
            return self.proxy
        parts = urlsplit(url)
        if parts.hostname and urllib.request.proxy_bypass(parts.hostname):
            return None
        return urllib.request.getproxies().get(parts.scheme) or None


def without_credentials(url: str) -> str:
    """A URL as it may be shown: a proxy's user and password are not."""
    parts = urlsplit(url)
    if "@" not in parts.netloc:
        return url
    return urlunsplit(parts._replace(netloc=parts.netloc.rsplit("@", 1)[1]))
