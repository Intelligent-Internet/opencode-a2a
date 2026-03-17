"""A2A wrapper for opencode."""

from importlib.metadata import PackageNotFoundError, version


def get_package_version() -> str:
    try:
        return version("opencode-a2a-server")
    except PackageNotFoundError:
        return "0.1.0"


__version__ = get_package_version()
