from importlib.metadata import PackageNotFoundError

from opencode_a2a import UNKNOWN_VERSION, get_package_version


def test_get_package_version_returns_unknown_when_metadata_is_missing(monkeypatch) -> None:
    def raise_package_not_found(_: str) -> str:
        raise PackageNotFoundError

    monkeypatch.setattr("opencode_a2a.version", raise_package_not_found)

    assert get_package_version() == UNKNOWN_VERSION
