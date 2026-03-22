"""A2A wrapper for opencode."""

from importlib.metadata import PackageNotFoundError, version

UNKNOWN_VERSION = "0+unknown"


def get_package_version() -> str:
    try:
        return version("opencode-a2a")
    except PackageNotFoundError:
        return UNKNOWN_VERSION


__version__ = get_package_version()
