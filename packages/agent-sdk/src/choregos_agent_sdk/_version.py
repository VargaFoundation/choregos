# SPDX-License-Identifier: Apache-2.0
from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("choregos-agent-sdk")
except PackageNotFoundError:  # running from a checkout that is not installed
    __version__ = "0+unknown"
