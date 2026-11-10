"""memchentic — self-hosted multi-user agent memory."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("memchentic")
except PackageNotFoundError:  # source tree imported without an installed distribution
    __version__ = "0.0.0+unknown"
